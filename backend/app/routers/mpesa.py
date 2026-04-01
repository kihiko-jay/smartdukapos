"""
M-PESA router (v4.0)

Critical fixes:
  1. WEBHOOK HARDENING: callback validated with HMAC-SHA256 signature
     (Safaricom IP whitelist enforced in nginx; signature as defence-in-depth)
  2. IDEMPOTENCY: callback uses SELECT FOR UPDATE to prevent two simultaneous
     callbacks for the same CheckoutRequestID from double-completing a transaction
  3. RACE CONDITION FIX: transaction status update is atomic (one DB write, one commit)
  4. OBSERVABILITY: every callback outcome logged with full context
  5. M-PESA FAILURE EVENTS: failed payments now notify the POS terminal via WebSocket
  6. STK push stores CheckoutRequestID on the transaction for correlation
"""

import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_cashier
from app.models.transaction import Transaction, TransactionStatus, PaymentMethod
from app.models.audit import AuditTrail
from app.services.mpesa import stk_push, query_stk_status
from app.core.notifier import notify_mpesa_confirmed, notify_mpesa_failed

logger = logging.getLogger("dukapos.mpesa")
router = APIRouter(prefix="/mpesa", tags=["M-PESA"])


class STKPushRequest(BaseModel):
    phone: str
    amount: float
    txn_number: str


class STKQueryRequest(BaseModel):
    checkout_request_id: str


# ── Webhook signature verification ───────────────────────────────────────────

def _verify_callback_signature(body: bytes, signature_header: str | None) -> bool:
    """
    Verify the Daraja callback HMAC-SHA256 signature.

    In production Safaricom does NOT send a signature header by default —
    enforce IP allowlisting in nginx instead. This guard is defence-in-depth
    for when a secret is configured.

    If MPESA_WEBHOOK_SECRET is not set, skip verification (backward compat).
    """
    secret = os.getenv("MPESA_WEBHOOK_SECRET", "")
    if not secret:
        return True   # No secret configured — rely on IP allowlisting
    if not signature_header:
        logger.warning("M-PESA callback received without signature header")
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ── STK Push ──────────────────────────────────────────────────────────────────

@router.post("/stk-push")
async def initiate_stk_push(
    payload: STKPushRequest,
    db: Session = Depends(get_db),
    _=Depends(require_cashier),
):
    """Trigger M-PESA Lipa Na M-PESA STK push to customer phone."""
    txn = db.query(Transaction).filter(Transaction.txn_number == payload.txn_number).first()
    if not txn:
        raise HTTPException(404, f"Transaction {payload.txn_number} not found")
    if txn.status != TransactionStatus.PENDING:
        raise HTTPException(400, f"Transaction is already {txn.status.value}")

    try:
        result = await stk_push(
            phone=payload.phone,
            amount=payload.amount,
            txn_number=payload.txn_number,
        )
    except Exception as e:
        logger.error("STK push failed for %s: %s", payload.txn_number, e)
        raise HTTPException(502, f"M-PESA STK push failed: {str(e)}")

    # Store the CheckoutRequestID on the transaction for callback correlation
    checkout_id = result.get("CheckoutRequestID")
    if checkout_id:
        txn.mpesa_checkout_id = checkout_id   # see migration 0004
        db.commit()

    logger.info("STK push initiated", extra={
        "txn_number":   payload.txn_number,
        "checkout_id":  checkout_id,
        "phone":        payload.phone[-4:] + "****",   # mask for logs
    })

    return {
        "message":             "STK push sent. Waiting for customer to enter PIN.",
        "checkout_request_id": checkout_id,
        "merchant_request_id": result.get("MerchantRequestID"),
        "txn_number":          payload.txn_number,
    }


# ── Poll STK Status ───────────────────────────────────────────────────────────

@router.post("/stk-query")
async def query_push_status(payload: STKQueryRequest, _=Depends(require_cashier)):
    """Poll Safaricom for the result of an STK push (backup if callback is slow)."""
    try:
        result = await query_stk_status(payload.checkout_request_id)
        return result
    except Exception as e:
        raise HTTPException(502, f"STK query failed: {str(e)}")


# ── Daraja Callback ───────────────────────────────────────────────────────────

@router.post("/callback")
async def mpesa_callback(request: Request, db: Session = Depends(get_db)):
    """
    Safaricom posts payment results here.

    CRITICAL SAFETY PROPERTIES:
      - Always returns {"ResultCode": 0, "ResultDesc": "Accepted"} to Safaricom
        regardless of internal errors — Safaricom will retry on non-200
      - SELECT FOR UPDATE prevents duplicate processing of the same callback
      - All DB writes are in a single transaction with rollback on error
      - Every outcome is logged with full context

    nginx config required in production:
      allow 196.201.214.200/29;   # Safaricom IP range
      deny all;
    """
    raw_body = await request.body()

    # ── Signature verification ─────────────────────────────────────────────
    sig_header = request.headers.get("X-Safaricom-Signature")
    if not _verify_callback_signature(raw_body, sig_header):
        logger.error("M-PESA callback: invalid signature — rejected")
        # Still return 200 to prevent Safaricom seeing an error
        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    try:
        body = await request.json()
    except Exception:
        logger.error("M-PESA callback: could not parse body")
        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    try:
        stk_callback  = body["Body"]["stkCallback"]
        result_code   = stk_callback["ResultCode"]
        checkout_req  = stk_callback["CheckoutRequestID"]
        merchant_req  = stk_callback["MerchantRequestID"]

        logger.info("M-PESA callback received", extra={
            "result_code":    result_code,
            "checkout_req_id": checkout_req,
        })

        if result_code == 0:
            # ── Payment successful ──────────────────────────────────────────
            metadata = {
                item["Name"]: item.get("Value")
                for item in stk_callback.get("CallbackMetadata", {}).get("Item", [])
            }
            mpesa_ref   = metadata.get("MpesaReceiptNumber")
            account_ref = metadata.get("AccountReference")   # our txn_number

            if not mpesa_ref or not account_ref:
                logger.error("M-PESA callback: missing MpesaReceiptNumber or AccountReference",
                             extra={"metadata": metadata})
                return {"ResultCode": 0, "ResultDesc": "Accepted"}

            # SELECT FOR UPDATE — prevents two simultaneous callbacks for the
            # same transaction from both succeeding (race condition fix)
            txn = (
                db.query(Transaction)
                .filter(Transaction.txn_number == account_ref)
                .with_for_update(skip_locked=True)   # skip if already locked by another callback
                .first()
            )

            if not txn:
                logger.error("M-PESA callback: txn %s not found", account_ref)
                return {"ResultCode": 0, "ResultDesc": "Accepted"}

            if txn.status != TransactionStatus.PENDING:
                # Already processed — idempotent, do nothing
                logger.info("M-PESA callback: txn %s already %s — skipping",
                            account_ref, txn.status.value)
                return {"ResultCode": 0, "ResultDesc": "Accepted"}

            try:
                txn.mpesa_ref    = mpesa_ref
                txn.status       = TransactionStatus.COMPLETED
                txn.completed_at = datetime.now(timezone.utc)

                db.add(AuditTrail(
                    store_id   = txn.store_id,
                    actor_name = "mpesa_callback",
                    action     = "mpesa_confirmed",
                    entity     = "transaction",
                    entity_id  = txn.txn_number,
                    before_val = {"status": "pending"},
                    after_val  = {"status": "completed", "mpesa_ref": mpesa_ref},
                    notes      = f"checkout_id={checkout_req}",
                ))
                db.commit()

                logger.info("M-PESA payment confirmed", extra={
                    "txn_number": account_ref,
                    "mpesa_ref":  mpesa_ref,
                })

                # Push WS notification — fire-and-forget, never fails the response
                terminal_id = txn.cashier.terminal_id if txn.cashier else None
                try:
                    await notify_mpesa_confirmed(terminal_id, txn.txn_number, mpesa_ref)
                except Exception as ws_err:
                    logger.warning("WS notify failed (non-fatal): %s", ws_err)

            except Exception as db_err:
                db.rollback()
                logger.error("M-PESA callback DB write failed: %s", db_err, exc_info=True)

        else:
            # ── Payment failed or cancelled ─────────────────────────────────
            # Correlate back to transaction via mpesa_checkout_id
            txn = (
                db.query(Transaction)
                .filter(Transaction.mpesa_checkout_id == checkout_req)
                .first()
            )

            logger.warning("M-PESA payment failed/cancelled", extra={
                "result_code":   result_code,
                "checkout_req":  checkout_req,
                "txn_number":    txn.txn_number if txn else "unknown",
            })

            terminal_id = (txn.cashier.terminal_id if txn and txn.cashier else None)
            try:
                await notify_mpesa_failed(
                    terminal_id,
                    txn.txn_number if txn else checkout_req,
                    result_code,
                )
            except Exception as ws_err:
                logger.warning("WS notify (payment failed) failed (non-fatal): %s", ws_err)

    except (KeyError, TypeError) as parse_err:
        logger.error("M-PESA callback parse error: %s | body: %s", parse_err, body)

    # Always return accepted to Safaricom
    return {"ResultCode": 0, "ResultDesc": "Accepted"}
