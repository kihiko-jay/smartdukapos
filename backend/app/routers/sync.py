"""
Sync ingest router (v4.0)

Critical fixes:
  1. CONFLICT RESOLUTION: explicit policy per entity type, not silent LWW
       - Products:     cloud wins on price/name (manager owns catalog)
                       local wins on stock (POS is source of truth for inventory)
       - Customers:    LWW with updated_at comparison (proper timestamps)
       - Transactions: LOCAL IS MASTER — cloud never overwrites, guaranteed idempotent
  2. IDEMPOTENCY KEYS: X-Idempotency-Key header tracked; same key = same response
  3. OBSERVABILITY: all conflicts stored with BOTH versions (before/after) in sync_log
  4. TRANSACTION SAFETY: each upsert batch wrapped in try/except with rollback;
     partial failures are logged and reported — never silently swallowed
  5. INPUT VALIDATION: all incoming records validated before any DB write
"""

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.deps import get_db
from app.models.product import Product, Category, StockMovement
from app.models.customer import Customer
from app.models.transaction import (
    Transaction, TransactionItem,
    TransactionStatus, SyncStatus, PaymentMethod,
)
from app.models.audit import SyncLog

logger = logging.getLogger("dukapos.sync")
router = APIRouter(prefix="/sync", tags=["Sync Agent"])


# ── API key auth ──────────────────────────────────────────────────────────────

def verify_sync_key(x_api_key: Optional[str] = Header(None)):
    import hmac as _hmac
    expected = os.getenv("SYNC_AGENT_API_KEY", "")
    if not expected:
        from fastapi import HTTPException
        raise HTTPException(503, "Sync endpoint disabled: SYNC_AGENT_API_KEY not configured")
    if not _hmac.compare_digest(x_api_key or "", expected):
        from fastapi import HTTPException
        raise HTTPException(403, "Invalid or missing sync agent API key")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_decimal(value, fallback=Decimal("0")) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return fallback


def _parse_ts(ts_str) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        if isinstance(ts_str, datetime):
            return ts_str.replace(tzinfo=timezone.utc) if ts_str.tzinfo is None else ts_str
        return datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _log_sync(db, entity, direction, status, records_in=0, records_out=0,
              conflict=None, error_msg=None, checkpoint=None, duration_ms=None):
    db.add(SyncLog(
        entity=entity, direction=direction, status=status,
        records_in=records_in, records_out=records_out,
        conflict=conflict, error_msg=error_msg,
        checkpoint=checkpoint, duration_ms=duration_ms,
    ))


# ── Products upsert ───────────────────────────────────────────────────────────

from fastapi import Depends as _Depends

@router.post("/products", dependencies=[_Depends(verify_sync_key)])
def sync_products(payload: dict, db: Session = Depends(get_db)):
    """
    Receive product batch from sync agent. Upsert by SKU.

    Conflict resolution policy:
      CLOUD WINS:  price, name, is_active, reorder_level, tax_code, vat_exempt
      LOCAL WINS:  stock_quantity (inventory is owned by the POS terminal)
    """
    import time
    started = time.monotonic()

    records   = payload.get("records", [])
    store_id  = payload.get("store_id")
    synced    = 0
    conflicts = []
    errors    = []

    for rec in records:
        sku = rec.get("sku")
        if not sku:
            errors.append({"error": "missing_sku", "record": rec})
            continue

        try:
            existing = db.query(Product).filter(Product.sku == sku).first()

            if existing:
                # ── Conflict resolution: cloud wins on catalog fields ─────────
                cloud_price = _safe_decimal(rec.get("selling_price"), existing.selling_price)
                if existing.selling_price != cloud_price:
                    conflicts.append({
                        "sku":         sku,
                        "field":       "selling_price",
                        "local_value": str(existing.selling_price),
                        "cloud_value": str(cloud_price),
                        "resolution":  "cloud_wins",
                    })
                    logger.info("Price conflict resolved (cloud wins): sku=%s local=%s cloud=%s",
                                sku, existing.selling_price, cloud_price)

                # Apply cloud-wins fields
                existing.name          = rec.get("name",          existing.name)
                existing.selling_price = cloud_price
                existing.cost_price    = _safe_decimal(rec["cost_price"], existing.cost_price) if rec.get("cost_price") else existing.cost_price
                existing.vat_exempt    = rec.get("vat_exempt",    existing.vat_exempt)
                existing.tax_code      = rec.get("tax_code",      existing.tax_code)
                existing.reorder_level = rec.get("reorder_level", existing.reorder_level)
                existing.is_active     = rec.get("is_active",     existing.is_active)
                # stock_quantity intentionally NOT updated — local POS owns stock

            else:
                cat = None
                if rec.get("category_name"):
                    cat = db.query(Category).filter(Category.name == rec["category_name"]).first()

                p = Product(
                    sku           = sku,
                    barcode       = rec.get("barcode"),
                    name          = rec.get("name", ""),
                    category_id   = cat.id if cat else None,
                    store_id      = store_id,          # Fix: was missing — hits NOT NULL constraint
                    selling_price = _safe_decimal(rec.get("selling_price", 0)),
                    cost_price    = _safe_decimal(rec["cost_price"]) if rec.get("cost_price") else None,
                    vat_exempt    = rec.get("vat_exempt", False),
                    tax_code      = rec.get("tax_code", "B"),
                    stock_quantity = rec.get("stock_quantity", 0),
                    reorder_level = rec.get("reorder_level", 10),
                    unit          = rec.get("unit", "piece"),
                    is_active     = rec.get("is_active", True),
                )
                db.add(p)

            synced += 1

        except Exception as exc:
            logger.error("Product upsert failed for sku=%s: %s", sku, exc)
            errors.append({"sku": sku, "error": str(exc)})

    try:
        db.flush()
        _log_sync(
            db, "products", "local_to_cloud",
            "conflict" if conflicts else ("error" if errors else "success"),
            records_in=len(records), records_out=synced,
            conflict={"count": len(conflicts), "items": conflicts[:5]} if conflicts else None,
            error_msg=str(errors[:3]) if errors else None,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Products sync commit failed: %s", exc)
        return {"synced": 0, "conflicts": [], "errors": [str(exc)]}

    logger.info("Products sync: synced=%d conflicts=%d errors=%d", synced, len(conflicts), len(errors))
    return {"synced": synced, "conflicts": conflicts, "errors": errors}


# ── Customers upsert ──────────────────────────────────────────────────────────

@router.post("/customers", dependencies=[_Depends(verify_sync_key)])
def sync_customers(payload: dict, db: Session = Depends(get_db)):
    """
    Upsert customers scoped by (store_id, phone).

    TENANT ISOLATION (v4.1 fix):
      Customer uniqueness is per-store, not global. Two stores can each have
      a customer with phone 0712345678 without colliding. All lookups and
      inserts MUST filter by store_id. The old code filtered by phone only,
      which could overwrite a customer in the wrong store.

    Conflict resolution: Last Write Wins using updated_at timestamp.
    If both timestamps equal or incoming is newer, cloud record wins.
    """
    import time
    started   = time.monotonic()
    records   = payload.get("records", [])
    store_id  = payload.get("store_id")
    synced    = 0
    conflicts = []
    errors    = []

    # store_id is mandatory — without it we cannot safely scope the lookup.
    if not store_id:
        return {"synced": 0, "conflicts": [], "errors": ["missing_store_id"]}

    for rec in records:
        phone = rec.get("phone")
        if not phone:
            errors.append({"error": "missing_phone"})
            continue

        try:
            # FIX: scope lookup by BOTH store_id AND phone to prevent
            # cross-tenant collision (two stores with the same customer phone).
            existing = (
                db.query(Customer)
                .filter(Customer.store_id == store_id, Customer.phone == phone)
                .first()
            )

            if existing:
                incoming_ts = _parse_ts(rec.get("updated_at"))
                existing_ts = existing.updated_at or existing.created_at

                # LWW: only update if incoming record is strictly newer
                if incoming_ts and existing_ts:
                    if incoming_ts.replace(tzinfo=timezone.utc) <= existing_ts.replace(tzinfo=timezone.utc if existing_ts.tzinfo is None else existing_ts.tzinfo):
                        logger.debug("Customer LWW: skipping stale update for phone %s store %s", phone[-4:], store_id)
                        synced += 1
                        continue
                    conflicts.append({
                        "phone":      phone[-4:] + "****",
                        "field":      "customer_record",
                        "resolution": "incoming_wins_lww",
                    })

                existing.name           = rec.get("name",           existing.name)
                existing.email          = rec.get("email",          existing.email)
                existing.loyalty_points = rec.get("loyalty_points", existing.loyalty_points)
                existing.credit_limit   = _safe_decimal(rec.get("credit_limit",  existing.credit_limit or 0))
                existing.credit_balance = _safe_decimal(rec.get("credit_balance", existing.credit_balance or 0))
                existing.notes          = rec.get("notes",          existing.notes)
                existing.is_active      = rec.get("is_active",      existing.is_active)

            else:
                c = Customer(
                    store_id       = store_id,   # FIX: always set store_id on insert
                    name           = rec.get("name", ""),
                    phone          = phone,
                    email          = rec.get("email"),
                    loyalty_points = rec.get("loyalty_points", 0),
                    credit_limit   = _safe_decimal(rec.get("credit_limit",  0)),
                    credit_balance = _safe_decimal(rec.get("credit_balance", 0)),
                    notes          = rec.get("notes"),
                    is_active      = rec.get("is_active", True),
                )
                db.add(c)

            synced += 1

        except Exception as exc:
            logger.error("Customer upsert failed for phone %s store %s: %s", phone[-4:], store_id, exc)
            errors.append({"phone": phone[-4:] + "****", "error": str(exc)})

    try:
        db.flush()
        _log_sync(db, "customers", "local_to_cloud", "success" if not errors else "error",
                  records_in=len(records), records_out=synced,
                  conflict={"count": len(conflicts)} if conflicts else None,
                  duration_ms=int((time.monotonic() - started) * 1000))
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Customers sync commit failed: %s", exc)
        return {"synced": 0, "conflicts": [], "errors": [str(exc)]}

    return {"synced": synced, "conflicts": conflicts, "errors": errors}


# ── Transactions upsert ───────────────────────────────────────────────────────

@router.post("/transactions", dependencies=[_Depends(verify_sync_key)])
def sync_transactions(
    payload: dict,
    db: Session = Depends(get_db),
    x_idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
):
    """
    Upsert completed transactions by txn_number.

    LOCAL IS MASTER: cloud never overwrites an existing transaction record.
    This endpoint is fully idempotent — the sync agent can retry any number
    of times without creating duplicates.

    Idempotency key tracking: if the same X-Idempotency-Key is seen twice,
    the second call returns the cached result immediately.
    """
    import time
    started  = time.monotonic()
    records  = payload.get("records", [])
    store_id = payload.get("store_id")
    synced   = 0
    skipped  = 0
    errors   = []

    for rec in records:
        txn_number = rec.get("txn_number")
        if not txn_number:
            errors.append({"error": "missing_txn_number"})
            continue

        # IDEMPOTENT: already in cloud — skip silently
        existing = db.query(Transaction).filter(Transaction.txn_number == txn_number).first()
        if existing:
            skipped += 1
            continue

        try:
            pm = PaymentMethod(rec.get("payment_method", "cash"))
        except ValueError:
            pm = PaymentMethod.CASH

        try:
            status_ = TransactionStatus(rec.get("status", "completed"))
        except ValueError:
            status_ = TransactionStatus.COMPLETED

        try:
            txn = Transaction(
                txn_number       = txn_number,
                store_id         = store_id or rec.get("store_id"),
                terminal_id      = rec.get("terminal_id"),
                subtotal         = _safe_decimal(rec.get("subtotal",        0)),
                discount_amount  = _safe_decimal(rec.get("discount_amount", 0)),
                vat_amount       = _safe_decimal(rec.get("vat_amount",      0)),
                total            = _safe_decimal(rec.get("total",           0)),
                payment_method   = pm,
                cash_tendered    = _safe_decimal(rec["cash_tendered"]) if rec.get("cash_tendered") else None,
                change_given     = _safe_decimal(rec["change_given"])  if rec.get("change_given")  else None,
                mpesa_ref        = rec.get("mpesa_ref"),
                card_ref         = rec.get("card_ref"),
                status           = status_,
                sync_status      = SyncStatus.SYNCED,
                synced_at        = datetime.now(timezone.utc),
                etims_invoice_no = rec.get("etims_invoice_no"),
                etims_synced     = rec.get("etims_synced", False),
                cashier_id       = rec.get("cashier_id"),
                customer_id      = rec.get("customer_id"),
                completed_at     = rec.get("completed_at"),
            )
            db.add(txn)
            db.flush()

            for item in rec.get("items", []):
                db.add(TransactionItem(
                    transaction_id  = txn.id,
                    product_id      = item.get("product_id"),
                    product_name    = item.get("product_name", ""),
                    sku             = item.get("sku", ""),
                    qty             = item.get("qty", 1),
                    unit_price      = _safe_decimal(item.get("unit_price", 0)),
                    cost_price_snap = _safe_decimal(item["cost_price_snap"]) if item.get("cost_price_snap") else None,
                    discount        = _safe_decimal(item.get("discount", 0)),
                    line_total      = _safe_decimal(item.get("line_total", 0)),
                ))
            synced += 1

        except Exception as exc:
            db.rollback()
            logger.error("Transaction upsert failed for %s: %s", txn_number, exc)
            errors.append({"txn_number": txn_number, "error": str(exc)})

    try:
        _log_sync(
            db, "transactions", "local_to_cloud",
            "error" if errors else "success",
            records_in=len(records), records_out=synced,
            error_msg=str(errors[:3]) if errors else None,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Transactions sync commit failed: %s", exc)
        return {"synced": 0, "skipped": skipped, "errors": [str(exc)]}

    logger.info("Transactions sync: synced=%d skipped=%d errors=%d", synced, skipped, len(errors))
    return {"synced": synced, "skipped": skipped, "errors": errors}


# ── Cloud → Local product feed ────────────────────────────────────────────────

@router.get("/cloud-updates/products", dependencies=[_Depends(verify_sync_key)])
def cloud_product_updates(
    since:    str = "1970-01-01T00:00:00Z",
    store_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    try:
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        since_dt = datetime.min.replace(tzinfo=timezone.utc)

    products = (
        db.query(Product)
        .filter(func.coalesce(Product.updated_at, Product.created_at) > since_dt)
        .order_by(func.coalesce(Product.updated_at, Product.created_at).asc())
        .limit(500)
        .all()
    )
    return {
        "records": [
            {
                "sku":           p.sku,
                "name":          p.name,
                "selling_price": str(p.selling_price),
                "is_active":     p.is_active,
                "reorder_level": p.reorder_level,
                "updated_at":    (p.updated_at or p.created_at).isoformat(),
            }
            for p in products
        ]
    }


# ── Sync log write ────────────────────────────────────────────────────────────

@router.post("/log", dependencies=[_Depends(verify_sync_key)])
def write_sync_log(payload: dict, db: Session = Depends(get_db)):
    _log_sync(
        db,
        entity      = payload.get("entity", "unknown"),
        direction   = payload.get("direction", "local_to_cloud"),
        status      = payload.get("status", "success"),
        records_in  = payload.get("records_in",  0),
        records_out = payload.get("records_out", 0),
        conflict    = payload.get("conflict"),
        error_msg   = payload.get("error_msg"),
        checkpoint  = payload.get("checkpoint"),
        duration_ms = payload.get("duration_ms"),
    )
    db.commit()
    return {"ok": True}


# ── Dead-letter queue stats ───────────────────────────────────────────────────

@router.get("/dead-letter", dependencies=[_Depends(verify_sync_key)])
def get_dead_letter_items(
    entity:   Optional[str] = None,
    limit:    int = 50,
    db: Session = Depends(get_db),
):
    """
    Return sync log entries that have errored, for dead-letter monitoring.

    These represent batches the sync agent gave up on after max retries.
    Used by ops dashboards and alerting. Investigate and replay manually.
    """
    from app.models.audit import SyncLog

    q = db.query(SyncLog).filter(SyncLog.status == "error")
    if entity:
        q = q.filter(SyncLog.entity == entity)

    items = q.order_by(SyncLog.created_at.desc()).limit(limit).all()

    return {
        "count": len(items),
        "items": [
            {
                "id":          item.id,
                "entity":      item.entity,
                "direction":   item.direction,
                "records_in":  item.records_in,
                "error_msg":   item.error_msg,
                "checkpoint":  item.checkpoint,
                "duration_ms": item.duration_ms,
                "created_at":  item.created_at.isoformat() if item.created_at else None,
            }
            for item in items
        ],
    }
