"""
Transactions router (v4.0)

Changes:
  - create_transaction and void_transaction wrapped in explicit try/except
    that rolls back on any failure — no partial writes
  - Idempotency-Key header checked on create: if the same key has already
    been processed, return the existing transaction (safe retries)
  - Logging added at key decision points
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Header
from sqlalchemy.orm import Session
from sqlalchemy import func, cast, Date
from typing import List, Optional
from datetime import datetime, date, timezone
from decimal import Decimal
import uuid

from app.core.deps import get_db, require_cashier, require_manager, get_current_employee
from app.database import business_date
from app.models.transaction import Transaction, TransactionItem, TransactionStatus, PaymentMethod, SyncStatus
from app.models.product import Product, StockMovement
from app.models.employee import Employee
from app.models.audit import AuditTrail
from app.schemas.transaction import TransactionCreate, TransactionOut, TransactionSummary
from app.core.config import settings

logger = logging.getLogger("dukapos.transactions")
router = APIRouter(prefix="/transactions", tags=["Transactions"])


def generate_txn_number() -> str:
    return f"TXN-{uuid.uuid4().hex[:8].upper()}"


def _write_audit(db, actor_id: int, actor_name: str, action: str, txn_number: str,
                 store_id: int = None, before=None, after=None):
    db.add(AuditTrail(
        store_id   = store_id,
        actor_id   = actor_id,
        actor_name = actor_name,
        action     = action,
        entity     = "transaction",
        entity_id  = txn_number,
        before_val = before,
        after_val  = after,
    ))


@router.post("", response_model=TransactionOut)
def create_transaction(
    payload:          TransactionCreate,
    db:               Session  = Depends(get_db),
    current:          Employee = Depends(require_cashier),
    idempotency_key:  Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """
    Create a completed transaction.

    Idempotency: pass Idempotency-Key header (e.g. the offline txn_number).
    If a transaction with this key was already processed, returns the existing
    record instead of creating a duplicate. Safe for retries.
    """

    # ── Idempotency check ──────────────────────────────────────────────────────
    if idempotency_key:
        existing = (
            db.query(Transaction)
            .filter(Transaction.txn_number == idempotency_key)
            .first()
        )
        if existing:
            logger.info("Idempotent request — returning existing txn", extra={
                "idempotency_key": idempotency_key, "txn_id": existing.id,
            })
            return existing

    try:
        # ── 1. Validate items and verify stock ─────────────────────────────────
        items_data = []
        subtotal   = Decimal("0.00")

        for item in payload.items:
            product = db.query(Product).filter(Product.id == item.product_id).with_for_update().first()
            if not product:
                raise HTTPException(404, f"Product ID {item.product_id} not found")
            if product.stock_quantity < item.qty:
                raise HTTPException(
                    400,
                    f"Insufficient stock for '{product.name}': "
                    f"requested {item.qty}, available {product.stock_quantity}",
                )
            line_total = (item.unit_price * item.qty) - item.discount
            subtotal  += line_total
            items_data.append((product, item, line_total))

        # ── 2. Calculate totals ────────────────────────────────────────────────
        subtotal    -= payload.discount_amount
        vat_rate     = Decimal(str(settings.VAT_RATE))
        vat_amount   = subtotal * vat_rate
        total        = subtotal + vat_amount
        change_given = None

        if payload.payment_method == PaymentMethod.CASH:
            if payload.cash_tendered is None or payload.cash_tendered < total:
                raise HTTPException(
                    400,
                    f"Cash tendered ({payload.cash_tendered}) is less than total ({total:.2f})",
                )
            change_given = (payload.cash_tendered - total).quantize(Decimal("0.01"))

        # ── 3. Create transaction ──────────────────────────────────────────────
        txn_number = idempotency_key or generate_txn_number()
        txn = Transaction(
            txn_number      = txn_number,
            store_id        = current.store_id,
            terminal_id     = payload.terminal_id,
            subtotal        = subtotal.quantize(Decimal("0.01")),
            discount_amount = payload.discount_amount,
            vat_amount      = vat_amount.quantize(Decimal("0.01")),
            total           = total.quantize(Decimal("0.01")),
            payment_method  = payload.payment_method,
            cash_tendered   = payload.cash_tendered,
            change_given    = change_given,
            status          = TransactionStatus.PENDING,
            sync_status     = SyncStatus.PENDING,
            cashier_id      = current.id,
            customer_id     = payload.customer_id,
        )
        db.add(txn)
        db.flush()

        # ── 4. Line items + stock ledger ───────────────────────────────────────
        for product, item, line_total in items_data:
            line_vat = (line_total * vat_rate).quantize(Decimal("0.01"))
            db.add(TransactionItem(
                transaction_id  = txn.id,
                product_id      = product.id,
                product_name    = product.name,
                sku             = product.sku,
                qty             = item.qty,
                unit_price      = item.unit_price,
                cost_price_snap = product.cost_price,
                discount        = item.discount,
                vat_amount      = line_vat,
                line_total      = line_total.quantize(Decimal("0.01")),
                tax_code        = product.tax_code,
                vat_exempt      = product.vat_exempt,
            ))
            qty_before = product.stock_quantity
            product.stock_quantity -= item.qty
            db.add(StockMovement(
                product_id    = product.id,
                store_id      = current.store_id,
                movement_type = "sale",
                qty_delta     = -item.qty,
                qty_before    = qty_before,
                qty_after     = product.stock_quantity,
                ref_id        = txn.txn_number,
                performed_by  = current.id,
            ))

        # ── 5. Final status ────────────────────────────────────────────────────
        if payload.payment_method == PaymentMethod.MPESA:
            txn.status = TransactionStatus.PENDING
        else:
            txn.status       = TransactionStatus.COMPLETED
            txn.completed_at = datetime.now(timezone.utc)

        _write_audit(
            db, current.id, current.full_name, "create", txn.txn_number,
            store_id=current.store_id,
            after={"total": str(txn.total), "payment_method": txn.payment_method.value},
        )
        db.commit()
        db.refresh(txn)

        logger.info("Transaction created", extra={
            "txn_number": txn.txn_number,
            "total":      str(txn.total),
            "method":     txn.payment_method.value,
            "cashier_id": current.id,
        })
        return txn

    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Transaction creation failed — rolled back", exc_info=True)
        raise HTTPException(500, "Transaction failed. No charge was made.") from exc


@router.get("", response_model=List[TransactionSummary])
def list_transactions(
    date_from:      Optional[date]              = Query(None),
    date_to:        Optional[date]              = Query(None),
    cashier_id:     Optional[int]               = None,
    payment_method: Optional[PaymentMethod]     = None,
    status:         Optional[TransactionStatus] = None,
    sync_status:    Optional[SyncStatus]        = None,
    skip:  int = 0,
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
    current: Employee = Depends(require_cashier),
):
    q = db.query(Transaction).filter(
        Transaction.store_id == current.store_id
    )
    if date_from:       q = q.filter(cast(Transaction.created_at, Date) >= date_from)
    if date_to:         q = q.filter(cast(Transaction.created_at, Date) <= date_to)
    if cashier_id:      q = q.filter(Transaction.cashier_id     == cashier_id)
    if payment_method:  q = q.filter(Transaction.payment_method == payment_method)
    if status:          q = q.filter(Transaction.status         == status)
    if sync_status:     q = q.filter(Transaction.sync_status    == sync_status)
    return q.order_by(Transaction.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/summary/today")
def today_summary(db: Session = Depends(get_db), current: Employee = Depends(require_cashier)):
    today = business_date()
    rows  = (
        db.query(Transaction)
        .filter(Transaction.store_id == current.store_id)
        .filter(cast(Transaction.created_at, Date) == today)
        .filter(Transaction.status == TransactionStatus.COMPLETED)
        .all()
    )
    total_sales = sum(float(t.total) for t in rows)
    total_vat   = sum(float(t.vat_amount) for t in rows)
    by_method: dict = {}
    for t in rows:
        m = t.payment_method.value
        by_method[m] = round(by_method.get(m, 0) + float(t.total), 2)
    unsynced = sum(1 for t in rows if t.sync_status != SyncStatus.SYNCED)
    return {
        "date":               str(today),
        "transaction_count":  len(rows),
        "total_sales":        round(total_sales, 2),
        "total_vat":          round(total_vat, 2),
        "net_sales":          round(total_sales - total_vat, 2),
        "by_payment_method":  by_method,
        "unsynced_count":     unsynced,
        "currency":           settings.CURRENCY,
    }


@router.get("/{txn_id}", response_model=TransactionOut)
def get_transaction(txn_id: int, db: Session = Depends(get_db), current: Employee = Depends(require_cashier)):
    txn = db.query(Transaction).filter(Transaction.id == txn_id).first()
    if not txn:
        raise HTTPException(404, "Transaction not found")
    if txn.store_id != current.store_id:
        raise HTTPException(403, "Access denied")
    return txn


@router.post("/{txn_id}/void", dependencies=[Depends(require_manager)])
def void_transaction(
    txn_id: int,
    db:     Session  = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    txn = db.query(Transaction).filter(Transaction.id == txn_id).first()
    if not txn:
        raise HTTPException(404, "Transaction not found")
    if txn.store_id != current.store_id:
        raise HTTPException(403, "Cannot void a transaction from another store")
    if txn.status == TransactionStatus.VOIDED:
        raise HTTPException(400, "Transaction already voided")

    try:
        for item in txn.items:
            product = db.query(Product).filter(Product.id == item.product_id).with_for_update().first()
            if product:
                qty_before             = product.stock_quantity
                product.stock_quantity += item.qty
                db.add(StockMovement(
                    product_id    = product.id,
                    store_id      = txn.store_id,
                    movement_type = "void_restore",
                    qty_delta     = item.qty,
                    qty_before    = qty_before,
                    qty_after     = product.stock_quantity,
                    ref_id        = txn.txn_number,
                    performed_by  = current.id,
                ))

        txn.status = TransactionStatus.VOIDED
        _write_audit(
            db, current.id, current.full_name, "void", txn.txn_number,
            store_id=txn.store_id,
            before={"status": "completed"},
            after={"status": "voided"},
        )
        db.commit()
        logger.info("Transaction voided", extra={"txn_number": txn.txn_number, "by": current.id})
        return {"message": f"Transaction {txn.txn_number} voided. Stock restored."}

    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Void failed — rolled back", extra={"txn_id": txn_id}, exc_info=True)
        raise HTTPException(500, "Void failed. No changes were made.") from exc


@router.post("/{txn_id}/mpesa-confirm", dependencies=[Depends(require_manager)])
def mpesa_confirm(
    txn_id:    int,
    mpesa_ref: str = Query(...),
    db:        Session  = Depends(get_db),
    current:   Employee = Depends(get_current_employee),
):
    """
    Manager-only manual M-PESA confirmation for exception handling.

    Use only when the Daraja callback has not arrived and the cashier
    has confirmed the M-PESA receipt verbally with the customer.
    Every use is recorded in the audit trail.
    """
    txn = db.query(Transaction).filter(Transaction.id == txn_id).first()
    if not txn:
        raise HTTPException(404, "Transaction not found")
    if txn.store_id != current.store_id:
        raise HTTPException(403, "Transaction belongs to a different store")
    if txn.status == TransactionStatus.COMPLETED:
        raise HTTPException(400, "Transaction already completed")
    if txn.status == TransactionStatus.VOIDED:
        raise HTTPException(400, "Cannot confirm a voided transaction")

    txn.mpesa_ref    = mpesa_ref
    txn.status       = TransactionStatus.COMPLETED
    txn.completed_at = datetime.now(timezone.utc)

    db.add(AuditTrail(
        store_id   = txn.store_id,
        actor_id   = current.id,
        actor_name = current.full_name,
        action     = "manual_mpesa_confirm",
        entity     = "transaction",
        entity_id  = txn.txn_number,
        before_val = {"status": "pending"},
        after_val  = {"status": "completed", "mpesa_ref": mpesa_ref,
                      "confirmed_by": current.full_name, "method": "manual"},
        notes      = "Manual M-PESA confirmation by manager",
    ))
    db.commit()

    logger.warning(
        "Manual M-PESA confirmation by employee %s for txn %s",
        current.id, txn.txn_number,
    )
    return {"message": "Payment confirmed", "txn_number": txn.txn_number}


@router.post("/sync/mark-synced", dependencies=[Depends(require_cashier)])
def mark_synced(txn_numbers: List[str], db: Session = Depends(get_db)):
    """Called by sync agent after confirmed cloud write."""
    from datetime import timezone
    updated = (
        db.query(Transaction)
        .filter(Transaction.txn_number.in_(txn_numbers))
        .all()
    )
    for txn in updated:
        txn.sync_status = SyncStatus.SYNCED
        txn.synced_at   = datetime.now(timezone.utc)
    db.commit()
    logger.info("Marked synced", extra={"count": len(updated)})
    return {"marked_synced": len(updated)}
