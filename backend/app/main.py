"""
DukaPOS FastAPI application entry point — v4.0

v4.0 changes (from v3 / v2.4):
  - Full store isolation enforced on all endpoints (products, reports,
    transactions, customers, categories, suppliers)
  - PLATFORM_OWNER role — bypasses store scoping for support/ops visibility
  - Per-store cache keys — Shop A cache never pollutes Shop B
  - Customer store_id FK — customers are now scoped to their shop
  - Per-store SKU and barcode uniqueness (migrations 0006 + 0007)
  - NUMERIC(12,2) on customer credit columns (float → exact decimal)
  - Store name/location sourced from DB record on Z-tape and reports
  - All previously suggested fixes applied and validated
"""

import logging
import os
import asyncio
from contextlib import asynccontextmanager
import hmac as _hmac

from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.core.middleware import RequestLoggingMiddleware
from app.core.versioning import APIVersionMiddleware
from app.core.cache import cache
from app.core.deps import init_rate_limiters
from app.core.pubsub import ws_pubsub
from app.database import verify_db_connection, engine
from app.core.notifier import manager as ws_manager
from app.core.metrics import metrics
from app.routers import (
    auth, products, transactions, reports, mpesa,
    etims, subscription, audit, sync, ws, platform,
)

setup_logging()
logger = logging.getLogger("dukapos.main")


def _init_sentry() -> None:
    dsn = settings.SENTRY_DSN
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        sentry_sdk.init(
            dsn=dsn,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
            traces_sample_rate=0.1,
            environment="production" if not settings.DEBUG else "development",
            release=settings.APP_VERSION,
        )
        logger.info("Sentry error tracking initialised")
    except ImportError:
        logger.info("sentry-sdk not installed — error tracking disabled")
    except Exception as exc:
        logger.warning("Sentry init failed (non-fatal): %s", exc)


_redis_sync_client = None


async def _init_redis() -> None:
    global _redis_sync_client
    if not settings.REDIS_URL:
        logger.info("REDIS_URL not set — cache and distributed rate limiting disabled")
        return
    await cache.init()
    try:
        import redis as _redis_sync
        _redis_sync_client = _redis_sync.from_url(
            settings.REDIS_URL,
            socket_timeout=2,
            socket_connect_timeout=2,
            retry_on_timeout=False,
        )
        _redis_sync_client.ping()
        logger.info("Redis sync client connected (rate limiters)")
    except Exception as exc:
        logger.warning("Redis sync client unavailable: %s", exc)
        _redis_sync_client = None
    init_rate_limiters(_redis_sync_client)
    await ws_pubsub.start(settings.REDIS_URL)


async def _cleanup_stale_mpesa() -> None:
    """
    Startup task: find PENDING M-PESA transactions older than 10 minutes,
    restore their stock movements, and mark them FAILED/VOIDED.

    Runs once at startup to handle any transactions that were abandoned during
    the previous process lifetime (e.g. crash before Daraja callback arrived).
    """
    from datetime import datetime, timedelta, timezone
    from app.database import SessionLocal
    from app.models.transaction import Transaction, TransactionStatus, PaymentMethod
    from app.models.product import Product, StockMovement

    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
        stale = (
            db.query(Transaction)
            .filter(
                Transaction.status         == TransactionStatus.PENDING,
                Transaction.payment_method == PaymentMethod.MPESA,
                Transaction.created_at     <  cutoff,
            )
            .all()
        )

        if not stale:
            logger.info("M-PESA cleanup: no stale pending transactions found")
            return

        logger.warning("M-PESA cleanup: voiding %d stale pending transactions", len(stale))

        for txn in stale:
            try:
                # Restore stock for each line item
                for item in txn.items:
                    product = (
                        db.query(Product)
                        .filter(Product.id == item.product_id)
                        .with_for_update()
                        .first()
                    )
                    if product:
                        qty_before             = product.stock_quantity
                        product.stock_quantity += item.qty
                        db.add(StockMovement(
                            product_id    = product.id,
                            store_id      = txn.store_id,
                            movement_type = "mpesa_timeout_restore",
                            qty_delta     = item.qty,
                            qty_before    = qty_before,
                            qty_after     = product.stock_quantity,
                            ref_id        = txn.txn_number,
                            performed_by  = None,
                        ))

                txn.status = TransactionStatus.VOIDED
                logger.info(
                    "M-PESA cleanup: voided stale txn %s (created %s)",
                    txn.txn_number, txn.created_at,
                )
            except Exception as exc:
                logger.error("M-PESA cleanup: failed to void txn %s: %s", txn.txn_number, exc)
                db.rollback()
                continue

        db.commit()
        logger.info("M-PESA cleanup complete: %d transactions voided", len(stale))

    except Exception as exc:
        db.rollback()
        logger.error("M-PESA cleanup task failed: %s", exc)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s API v%s (DukaPOS v4)", settings.APP_NAME, settings.APP_VERSION)
    _init_sentry()
    await _init_redis()
    verify_db_connection()

    # Clean up any M-PESA transactions left PENDING from a previous run
    await _cleanup_stale_mpesa()

    sync_key = os.getenv("SYNC_AGENT_API_KEY", "")
    if sync_key and sync_key != "disabled":
        logger.info("Sync agent API key configured")

    if not os.getenv("MPESA_WEBHOOK_SECRET"):
        logger.warning(
            "MPESA_WEBHOOK_SECRET not set. "
            "Webhook signature verification disabled — ensure nginx IP allowlist is active."
        )

    logger.info("✅ %s started — docs: http://localhost:8000/docs", settings.APP_NAME)
    yield

    logger.info("Shutting down. Final metrics: %s", metrics.snapshot())
    await cache.close()
    await ws_pubsub.stop()
    logger.info("Shutting down %s", settings.APP_NAME)


app = FastAPI(
    title=f"{settings.APP_NAME} API",
    version=settings.APP_VERSION,
    description=(
        "DukaPOS v4.0 — Cloud-native POS & Retail Management for the Kenyan market.\n\n"
        "v4.0: Full store isolation on all endpoints, PLATFORM_OWNER role, "
        "customer store_id, per-store cache keys, NUMERIC money precision, "
        "per-store SKU/barcode uniqueness, platform admin endpoints."
    ),
    lifespan=lifespan,
)

app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(APIVersionMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PREFIX = "/api/v1"
app.include_router(auth.router,         prefix=PREFIX)
app.include_router(products.router,     prefix=PREFIX)
app.include_router(transactions.router, prefix=PREFIX)
app.include_router(reports.router,      prefix=PREFIX)
app.include_router(mpesa.router,        prefix=PREFIX)
app.include_router(etims.router,        prefix=PREFIX)
app.include_router(subscription.router, prefix=PREFIX)
app.include_router(audit.router,        prefix=PREFIX)
app.include_router(sync.router,         prefix=PREFIX)
app.include_router(platform.router,     prefix=PREFIX)   # NEW: platform owner only
app.include_router(ws.router)


def _require_internal_key(x_internal_key: str = Header(None)):
    expected = settings.INTERNAL_API_KEY
    if not expected:
        return
    if not _hmac.compare_digest(x_internal_key or "", expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}


@app.get("/health/deep", tags=["Health"])
def health_deep(_: None = Depends(_require_internal_key)):
    from sqlalchemy import text
    db_ok = False
    db_error = None
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:
        db_error = str(exc)
    return {
        "status":          "ok" if db_ok else "degraded",
        "app":             settings.APP_NAME,
        "version":         settings.APP_VERSION,
        "db":              "ok" if db_ok else f"error: {db_error}",
        "cache":           "ok" if cache.enabled else "disabled",
        "ws_terminals":    len(ws_manager.connected_terminals),
        "metrics":         metrics.snapshot(),
    }


@app.get("/metrics", tags=["Observability"])
def get_metrics(_: None = Depends(_require_internal_key)):
    snap  = metrics.snapshot()
    lines = []
    for key, val in snap.get("counters", {}).items():
        safe = key.replace(".", "_").replace("-", "_").replace(",", "_").replace("=", "_")
        lines.append(f"dukapos_{safe} {val}")
    return {**snap, "prometheus": "\n".join(lines)}
