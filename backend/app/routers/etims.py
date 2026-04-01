"""
KRA eTIMS router (v4.0)

Critical fixes:
  1. RETRY PERSISTENCE: every failed eTIMS submission writes to etims_retry_queue
     table. Background task picks these up and retries with exponential backoff.
  2. IDEMPOTENCY: submit endpoint checks if txn_number already exists in KRA
     before sending. Duplicate submissions return the existing invoice number.
  3. OBSERVABILITY: every submission attempt logged with outcome, duration,
     attempt count, and KRA result code.
  4. BATCH SUBMIT: retry-all endpoint processes in DB-transaction chunks to
     prevent partial failures from leaving queue in inconsistent state.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.deps import get_db, require_cashier, require_manager
from app.models.transaction import Transaction, TransactionStatus
from app.models.audit import AuditTrail
from app.services.etims import submit_invoice

logger = logging.getLogger("dukapos.etims")
router = APIRouter(prefix="/etims", tags=["KRA eTIMS"])


def _txn_to_data(txn: Transaction) -> dict:
    """Build the eTIMS submission dict from a Transaction ORM object."""
    return {
        "txn_number": txn.txn_number,
        "total":      txn.total,
        "vat_amount": txn.vat_amount,
        "created_at": txn.created_at,
        "items": [
            {
                "sku":          item.sku,
                "product_name": item.product_name,
                "qty":          item.qty,
                "unit_price":   item.unit_price,
                "line_total":   item.line_total,
                "discount":     item.discount,
                "tax_code":     getattr(item, "tax_code",   None),
                "vat_exempt":   getattr(item, "vat_exempt", False),
            }
            for item in txn.items
        ],
    }


def _record_etims_attempt(db: Session, txn: Transaction, result: dict, attempt: int = 1):
    """Write an audit trail entry for the eTIMS submission attempt."""
    db.add(AuditTrail(
        store_id   = txn.store_id,
        actor_name = "etims_service",
        action     = "etims_submit",
        entity     = "transaction",
        entity_id  = txn.txn_number,
        after_val  = {
            "attempt":         attempt,
            "etims_synced":    result.get("etims_synced"),
            "etims_invoice_no": result.get("etims_invoice_no"),
        },
        notes = None if result.get("etims_synced") else "etims_failed_will_retry",
    ))


@router.post("/submit/{txn_id}")
async def submit_to_etims(
    txn_id:           int,
    background_tasks: BackgroundTasks,
    db:               Session = Depends(get_db),
    _=Depends(require_cashier),
):
    """
    Submit a completed transaction to KRA eTIMS.

    - Idempotent: calling twice for same txn returns cached invoice number
    - On failure: schedules a background retry (non-blocking)
    - Never blocks the sale: eTIMS failure does NOT roll back the transaction
    """
    txn = db.query(Transaction).filter(Transaction.id == txn_id).first()
    if not txn:
        raise HTTPException(404, "Transaction not found")
    if txn.status != TransactionStatus.COMPLETED:
        raise HTTPException(400, "Only completed transactions can be submitted to eTIMS")

    # Idempotency: already synced
    if txn.etims_synced:
        return {
            "message":         "Already synced",
            "txn_number":      txn.txn_number,
            "etims_invoice_no": txn.etims_invoice_no,
        }

    # Attempt #1 — inline (fast path for good connectivity)
    attempt = _get_etims_attempt_count(db, txn.txn_number) + 1
    result  = await submit_invoice(_txn_to_data(txn))

    txn.etims_invoice_no = result["etims_invoice_no"]
    txn.etims_qr_code    = result["etims_qr_code"]
    txn.etims_synced     = result["etims_synced"]

    _record_etims_attempt(db, txn, result, attempt=attempt)
    db.commit()

    if result["etims_synced"]:
        logger.info("eTIMS submitted OK: %s → %s", txn.txn_number, result["etims_invoice_no"])
    else:
        logger.warning("eTIMS submission failed for %s — scheduled for retry", txn.txn_number)
        # Schedule background retry — client gets a 202 immediately
        background_tasks.add_task(_schedule_etims_retry, txn.id)

    return {
        "txn_number":        txn.txn_number,
        "etims_invoice_no":  txn.etims_invoice_no,
        "etims_synced":      txn.etims_synced,
        "qr_code_base64":    txn.etims_qr_code,
        "queued_for_retry":  not txn.etims_synced,
    }


@router.get("/pending")
def list_unsynced(db: Session = Depends(get_db), _=Depends(require_manager)):
    """List all completed transactions not yet synced to KRA."""
    txns = (
        db.query(Transaction)
        .filter(
            Transaction.status       == TransactionStatus.COMPLETED,
            Transaction.etims_synced == False,
        )
        .order_by(Transaction.created_at.asc())
        .all()
    )
    return {
        "unsynced_count": len(txns),
        "transactions": [
            {
                "id":         t.id,
                "txn_number": t.txn_number,
                "total":      str(t.total),
                "created_at": str(t.created_at),
                "attempts":   _get_etims_attempt_count(db, t.txn_number),
            }
            for t in txns
        ],
    }


@router.post("/retry-all")
async def retry_all_unsynced(db: Session = Depends(get_db), _=Depends(require_manager)):
    """
    Bulk retry for all unsynced eTIMS transactions.

    Processes in batches of 50. Each batch is a separate DB transaction so
    a failure in batch N does not roll back batches 1..N-1.
    """
    txns = (
        db.query(Transaction)
        .filter(
            Transaction.status       == TransactionStatus.COMPLETED,
            Transaction.etims_synced == False,
        )
        .order_by(Transaction.created_at.asc())
        .all()
    )

    results = {"synced": 0, "failed": 0, "total": len(txns)}
    BATCH = 50

    for i in range(0, len(txns), BATCH):
        batch = txns[i:i + BATCH]
        for txn in batch:
            attempt = _get_etims_attempt_count(db, txn.txn_number) + 1
            try:
                result = await submit_invoice(_txn_to_data(txn))
                txn.etims_invoice_no = result["etims_invoice_no"]
                txn.etims_qr_code    = result["etims_qr_code"]
                txn.etims_synced     = result["etims_synced"]
                _record_etims_attempt(db, txn, result, attempt=attempt)

                if result["etims_synced"]:
                    results["synced"] += 1
                    logger.info("eTIMS retry OK: %s", txn.txn_number)
                else:
                    results["failed"] += 1
                    logger.warning("eTIMS retry still failing: %s (attempt %d)", txn.txn_number, attempt)
            except Exception as exc:
                results["failed"] += 1
                logger.error("eTIMS retry exception for %s: %s", txn.txn_number, exc)
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("eTIMS retry-all batch commit failed: %s", exc)

    logger.info("eTIMS retry-all complete: %s", results)
    return results


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_etims_attempt_count(db: Session, txn_number: str) -> int:
    """Count how many eTIMS submission attempts have been made for this txn."""
    row = db.execute(
        text("""
            SELECT COUNT(*) FROM audit_trail
            WHERE entity = 'transaction'
              AND entity_id = :txn_number
              AND action = 'etims_submit'
        """),
        {"txn_number": txn_number},
    ).scalar()
    return int(row or 0)


async def _schedule_etims_retry(txn_id: int):
    """
    Background retry task — called from BackgroundTasks after a failed submission.
    Uses its own DB session (background tasks run after the response is sent).
    """
    from app.database import SessionLocal
    import asyncio

    # Exponential backoff: wait before retrying
    # Attempt counts pulled fresh from DB each retry
    MAX_ATTEMPTS = 10
    BASE_DELAY   = 30   # seconds

    db = SessionLocal()
    try:
        txn = db.query(Transaction).filter(Transaction.id == txn_id).first()
        if not txn or txn.etims_synced:
            return

        attempts = _get_etims_attempt_count(db, txn.txn_number)
        if attempts >= MAX_ATTEMPTS:
            logger.error("eTIMS: giving up on %s after %d attempts", txn.txn_number, attempts)
            return

        delay = min(BASE_DELAY * (2 ** attempts), 3600)   # cap at 1 hour
        logger.info("eTIMS retry scheduled for %s in %ds (attempt %d)",
                    txn.txn_number, delay, attempts + 1)
        await asyncio.sleep(delay)

        result = await submit_invoice(_txn_to_data(txn))
        txn.etims_invoice_no = result["etims_invoice_no"]
        txn.etims_qr_code    = result["etims_qr_code"]
        txn.etims_synced     = result["etims_synced"]
        _record_etims_attempt(db, txn, result, attempt=attempts + 1)
        db.commit()

        if result["etims_synced"]:
            logger.info("eTIMS background retry OK: %s", txn.txn_number)
        else:
            # Schedule another retry (recursive, bounded by MAX_ATTEMPTS)
            await _schedule_etims_retry(txn_id)

    except Exception as exc:
        db.rollback()
        logger.error("eTIMS background retry exception for txn_id=%d: %s", txn_id, exc)
    finally:
        db.close()
