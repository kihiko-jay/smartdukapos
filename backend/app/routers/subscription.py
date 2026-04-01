"""
Subscription management router.

Handles:
  - Store registration (creates store + admin account)
  - Plan info / current subscription status
  - M-PESA payment to upgrade
  - M-PESA callback to activate premium
"""

import hashlib
import hmac
import json
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone, timedelta

from app.core.deps import get_db, get_current_employee, require_admin, require_platform_owner
from app.core.security import hash_password
from app.models.subscription import Store, SubPayment, Plan, SubStatus
from app.models.employee import Employee, Role
from app.core.deps import _plan_details

import logging

router = APIRouter(prefix="/subscription", tags=["Subscription"])

logger = logging.getLogger(__name__)


def _verify_subscription_callback_signature(body: bytes, signature_header: str | None) -> bool:
    """
    Verify the Daraja callback HMAC-SHA256 signature for subscription payments.

    Mirrors the identical check in app/routers/mpesa.py — any change there
    must be reflected here too.

    If MPESA_WEBHOOK_SECRET is not set, verification is skipped (backward
    compatibility — relies on nginx IP allowlisting as the primary guard).
    """
    secret = os.getenv("MPESA_WEBHOOK_SECRET", "")
    if not secret:
        return True   # No secret configured — rely on IP allowlisting
    if not signature_header:
        logger.warning("Subscription M-PESA callback received without signature header")
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)

PLAN_PRICES = {
    Plan.STARTER: 1500,
    Plan.GROWTH:  3500,
    Plan.PRO:     7500,
}
TRIAL_DAYS = 14


# ── Schemas ───────────────────────────────────────────────────────────────────

class RegisterStoreRequest(BaseModel):
    store_name:     str
    store_location: Optional[str] = None
    kra_pin:        Optional[str] = None
    admin_name:     str
    admin_email:    str
    admin_password: str
    admin_phone:    Optional[str] = None
    mpesa_phone:    Optional[str] = None   # for billing


class UpgradeRequest(BaseModel):
    plan:        Plan
    months:      int  = 1
    mpesa_phone: str       # phone to send STK push to


# ── Register a new store (free, 14-day trial) ─────────────────────────────────

@router.post("/register", summary="Register new store — starts 14-day trial")
def register_store(payload: RegisterStoreRequest, db: Session = Depends(get_db)):
    # Check email not already taken
    if db.query(Employee).filter(Employee.email == payload.admin_email).first():
        raise HTTPException(400, "Email already registered")

    # Create store with trial
    trial_end = datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS)
    store = Store(
        name          = payload.store_name,
        location      = payload.store_location,
        kra_pin       = payload.kra_pin,
        plan          = Plan.FREE,
        sub_status    = SubStatus.TRIALING,
        trial_ends_at = trial_end,
        mpesa_phone   = payload.mpesa_phone,
    )
    db.add(store)
    db.flush()

    # Create admin employee for this store
    admin = Employee(
        store_id  = store.id,
        full_name = payload.admin_name,
        email     = payload.admin_email,
        phone     = payload.admin_phone,
        password  = hash_password(payload.admin_password),
        role      = Role.ADMIN,
    )
    db.add(admin)
    db.commit()
    db.refresh(store)

    return {
        "message":      f"Store registered. {TRIAL_DAYS}-day free trial started.",
        "store_id":     store.id,
        "store_name":   store.name,
        "trial_ends":   str(trial_end.date()),
        "admin_email":  payload.admin_email,
    }


# ── Get current subscription status ──────────────────────────────────────────

@router.get("/status")
def get_status(
    current: Employee = Depends(get_current_employee),
    db:      Session  = Depends(get_db),
):
    if not current.store_id:
        return {"plan": "free", "is_premium": False, "message": "No store linked to this account."}

    store = db.query(Store).filter(Store.id == current.store_id).first()
    if not store:
        raise HTTPException(404, "Store not found")

    now = datetime.now(timezone.utc)
    days_left = None

    if store.sub_status == SubStatus.TRIALING and store.trial_ends_at:
        days_left = max(0, (store.trial_ends_at - now).days)
    elif store.sub_status == SubStatus.ACTIVE and store.sub_ends_at:
        days_left = max(0, (store.sub_ends_at - now).days)

    return {
        "store_id":     store.id,
        "store_name":   store.name,
        "plan":         store.plan,
        "plan_label":   store.plan_label,
        "status":       store.sub_status,
        "is_premium":   store.is_premium,
        "days_left":    days_left,
        "trial_ends":   str(store.trial_ends_at.date()) if store.trial_ends_at else None,
        "sub_ends":     str(store.sub_ends_at.date())   if store.sub_ends_at   else None,
        "available_plans": _plan_details(),
    }


# ── Initiate upgrade via M-PESA ───────────────────────────────────────────────

@router.post("/upgrade")
async def initiate_upgrade(
    payload:  UpgradeRequest,
    current:  Employee = Depends(require_admin),
    db:       Session  = Depends(get_db),
):
    if payload.plan == Plan.FREE:
        raise HTTPException(400, "Cannot upgrade to Free plan.")

    price    = PLAN_PRICES[payload.plan] * payload.months
    store    = db.query(Store).filter(Store.id == current.store_id).first()
    if not store:
        raise HTTPException(404, "Store not found")

    # Create pending payment record
    payment = SubPayment(
        store_id    = store.id,
        amount      = price,
        plan        = payload.plan,
        months      = payload.months,
        status      = "pending",
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    # Trigger M-PESA STK push
    try:
        from app.services.mpesa import stk_push
        result = await stk_push(
            phone      = payload.mpesa_phone,
            amount     = price,
            txn_number = f"SUB-{payment.id}",
        )
        return {
            "message":            f"STK push sent to {payload.mpesa_phone}. Enter your M-PESA PIN to activate {payload.plan.value} plan.",
            "amount":             price,
            "plan":               payload.plan,
            "months":             payload.months,
            "payment_id":         payment.id,
            "checkout_request_id": result.get("CheckoutRequestID"),
        }
    except Exception as e:
        raise HTTPException(502, f"M-PESA STK push failed: {str(e)}")


# ── M-PESA callback — activates the plan ─────────────────────────────────────

@router.post("/mpesa-callback")
async def subscription_mpesa_callback(request: Request, db: Session = Depends(get_db)):
    """
    Safaricom posts here after payment.
    Set MPESA_CALLBACK_URL_SUBSCRIPTION in .env to point here.

    Security: HMAC-SHA256 signature is verified when MPESA_WEBHOOK_SECRET is
    set. If the secret is not configured the check is skipped for backward
    compatibility — nginx IP allowlisting is assumed to be the outer guard.
    """
    raw_body   = await request.body()
    sig_header = request.headers.get("X-Mpesa-Signature")

    if not _verify_subscription_callback_signature(raw_body, sig_header):
        logger.error(
            "Subscription M-PESA callback: invalid signature — request rejected",
            extra={"signature_header": sig_header},
        )
        raise HTTPException(status_code=400, detail="Invalid callback signature")

    logger.info(
        "Subscription M-PESA callback: signature verified (or secret not configured)",
        extra={"signature_present": sig_header is not None},
    )

    try:
        request_body = json.loads(raw_body)
    except Exception:
        logger.error("Subscription M-PESA callback: could not parse request body as JSON")
        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    try:
        stk = request_body["Body"]["stkCallback"]
        if stk["ResultCode"] != 0:
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        metadata  = {i["Name"]: i.get("Value") for i in stk["CallbackMetadata"]["Item"]}
        mpesa_ref = metadata.get("MpesaReceiptNumber")
        acct_ref  = metadata.get("AccountReference", "")   # SUB-{payment_id}

        if not acct_ref.startswith("SUB-"):
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        payment_id = int(acct_ref.split("-")[1])
        payment    = db.query(SubPayment).filter(SubPayment.id == payment_id).first()

        if payment and payment.status == "pending":
            payment.mpesa_ref = mpesa_ref
            payment.status    = "confirmed"

            # Activate the store's plan
            store = db.query(Store).filter(Store.id == payment.store_id).first()
            if store:
                now = datetime.now(timezone.utc)
                # If renewing, extend from current expiry; otherwise start now
                base = (store.sub_ends_at if store.sub_ends_at and store.sub_ends_at > now else now)
                store.plan        = payment.plan
                store.sub_status  = SubStatus.ACTIVE
                store.sub_ends_at = base + timedelta(days=30 * payment.months)

            db.commit()

    except Exception as e:
        logger.exception("Subscription callback processing error: %s", e)

    return {"ResultCode": 0, "ResultDesc": "Accepted"}


# ── Admin: manually activate (for cash/bank payments) ────────────────────────
# SECURITY (v4.1): restricted to PLATFORM_OWNER only.
#
# Previously used require_admin, which allowed any store admin to activate
# ANY store by guessing or knowing its ID — a tenant-boundary violation.
# require_platform_owner ensures only the global operator account can call this.

@router.post("/activate/{store_id}", dependencies=[Depends(require_platform_owner)])
def manually_activate(
    store_id: int,
    plan:     Plan,
    months:   int = 1,
    db: Session = Depends(get_db),
):
    """
    Manually activate a store subscription (cash/bank payment path).
    Platform owner only — store admins are explicitly excluded.
    """
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(404, "Store not found")

    now       = datetime.now(timezone.utc)
    base      = store.sub_ends_at if store.sub_ends_at and store.sub_ends_at > now else now
    store.plan        = plan
    store.sub_status  = SubStatus.ACTIVE
    store.sub_ends_at = base + timedelta(days=30 * months)
    db.commit()

    return {"message": f"Store {store.name} activated on {plan.value} plan until {store.sub_ends_at.date()}"}
