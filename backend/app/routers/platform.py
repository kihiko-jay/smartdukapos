"""
Platform Owner router — v4.0

Endpoints only accessible to PLATFORM_OWNER role.
These give you (the app developer) operational visibility:
  - List all stores and their subscription status
  - See which stores are active/trialing/expired
  - See aggregate platform metrics (total transactions, revenue by plan)
  - Manually activate a store (e.g. after bank transfer payment)
  - Suspend a store (non-payment or abuse)

What this router deliberately does NOT expose:
  - Individual store transaction details
  - Store product catalogs
  - Store customer lists
  - Store reports

Those belong to each shop owner. You can see operational metadata only.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from app.core.deps import get_db, require_platform_owner
from app.models.subscription import Store, SubPayment, Plan, SubStatus
from app.models.employee import Employee
from app.core.security import hash_password

logger = logging.getLogger("dukapos.platform")
router = APIRouter(prefix="/platform", tags=["Platform Owner"])


# ── All stores overview ───────────────────────────────────────────────────────

@router.get("/stores")
def list_all_stores(
    plan:     Optional[str] = Query(default=None, description="Filter by plan"),
    status:   Optional[str] = Query(default=None, description="Filter by sub_status"),
    skip:     int = 0,
    limit:    int = Query(default=50, le=200),
    db:       Session = Depends(get_db),
    _=Depends(require_platform_owner),
):
    """
    List all shops on the platform with their subscription status.
    Includes trial expiry, plan, last payment, and employee count.
    Does NOT include transactions, products, or customers.
    """
    q = db.query(Store)
    if plan:   q = q.filter(Store.plan       == plan)
    if status: q = q.filter(Store.sub_status == status)

    stores = q.order_by(Store.created_at.desc()).offset(skip).limit(limit).all()

    now = datetime.now(timezone.utc)
    result = []
    for s in stores:
        days_left = None
        if s.sub_status == SubStatus.TRIALING and s.trial_ends_at:
            days_left = max(0, (s.trial_ends_at - now).days)
        elif s.sub_status == SubStatus.ACTIVE and s.sub_ends_at:
            days_left = max(0, (s.sub_ends_at - now).days)

        emp_count = db.query(func.count(Employee.id)).filter(
            Employee.store_id == s.id, Employee.is_active == True
        ).scalar()

        result.append({
            "store_id":     s.id,
            "name":         s.name,
            "location":     s.location,
            "kra_pin":      s.kra_pin,
            "phone":        s.phone,
            "email":        s.email,
            "plan":         s.plan,
            "plan_label":   s.plan_label,
            "sub_status":   s.sub_status,
            "is_premium":   s.is_premium,
            "days_left":    days_left,
            "trial_ends":   s.trial_ends_at.isoformat() if s.trial_ends_at else None,
            "sub_ends":     s.sub_ends_at.isoformat()   if s.sub_ends_at   else None,
            "is_active":    s.is_active,
            "employee_count": emp_count,
            "registered":   s.created_at.isoformat() if s.created_at else None,
        })

    return {
        "total":  db.query(func.count(Store.id)).scalar(),
        "stores": result,
    }


# ── Platform metrics ──────────────────────────────────────────────────────────

@router.get("/metrics")
def platform_metrics(
    db: Session = Depends(get_db),
    _=Depends(require_platform_owner),
):
    """
    High-level platform health numbers.
    Aggregate counts only — no store-specific data.
    """
    now = datetime.now(timezone.utc)

    by_plan = db.execute(text("""
        SELECT plan, sub_status, COUNT(*) as count
        FROM stores
        WHERE is_active = TRUE
        GROUP BY plan, sub_status
        ORDER BY plan, sub_status
    """)).fetchall()

    expiring_soon = db.query(func.count(Store.id)).filter(
        Store.sub_status == SubStatus.TRIALING,
        Store.trial_ends_at <= now + timedelta(days=3),
        Store.trial_ends_at > now,
    ).scalar()

    expired_trials = db.query(func.count(Store.id)).filter(
        Store.sub_status == SubStatus.TRIALING,
        Store.trial_ends_at <= now,
    ).scalar()

    total_payments = db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE status='confirmed') as confirmed,
            COALESCE(SUM(amount) FILTER (WHERE status='confirmed'), 0) as total_revenue
        FROM sub_payments
    """)).fetchone()

    return {
        "stores_by_plan":       [{"plan": r.plan, "status": r.sub_status, "count": r.count} for r in by_plan],
        "trials_expiring_soon": expiring_soon,
        "expired_trials":       expired_trials,
        "total_confirmed_payments": total_payments.confirmed,
        "total_revenue_kes":    float(total_payments.total_revenue),
    }


# ── Subscription management ───────────────────────────────────────────────────

@router.post("/stores/{store_id}/activate")
def activate_store(
    store_id: int,
    plan:     Plan,
    months:   int = 1,
    db:       Session = Depends(get_db),
    _=Depends(require_platform_owner),
):
    """Manually activate a store (e.g. cash payment received)."""
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(404, "Store not found")

    now  = datetime.now(timezone.utc)
    base = store.sub_ends_at if (store.sub_ends_at and store.sub_ends_at > now) else now

    store.plan        = plan
    store.sub_status  = SubStatus.ACTIVE
    store.sub_ends_at = base + timedelta(days=30 * months)
    db.commit()

    logger.info("Platform: activated store %d on %s for %d months", store_id, plan, months)
    return {
        "message":  f"Store '{store.name}' activated on {plan.value} until {store.sub_ends_at.date()}",
        "store_id": store_id,
        "plan":     plan,
        "sub_ends": store.sub_ends_at.isoformat(),
    }


@router.post("/stores/{store_id}/suspend")
def suspend_store(
    store_id: int,
    reason:   str = Query(..., description="Reason for suspension"),
    db:       Session = Depends(get_db),
    _=Depends(require_platform_owner),
):
    """Suspend a store (non-payment, abuse, etc.)."""
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(404, "Store not found")

    store.is_active  = False
    store.sub_status = SubStatus.CANCELLED
    db.commit()

    logger.warning("Platform: suspended store %d — reason: %s", store_id, reason)
    return {"message": f"Store '{store.name}' suspended. Reason: {reason}"}


@router.post("/stores/{store_id}/reinstate")
def reinstate_store(
    store_id: int,
    db:       Session = Depends(get_db),
    _=Depends(require_platform_owner),
):
    """Reinstate a suspended store."""
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(404, "Store not found")

    store.is_active  = True
    store.sub_status = SubStatus.TRIALING
    store.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=14)
    db.commit()

    return {"message": f"Store '{store.name}' reinstated with 14-day trial"}


# ── Payment history ───────────────────────────────────────────────────────────

@router.get("/payments")
def payment_history(
    status: Optional[str] = Query(default=None),
    skip:   int = 0,
    limit:  int = Query(default=50, le=200),
    db:     Session = Depends(get_db),
    _=Depends(require_platform_owner),
):
    """All M-PESA subscription payments across all stores."""
    q = db.query(SubPayment)
    if status:
        q = q.filter(SubPayment.status == status)

    payments = q.order_by(SubPayment.created_at.desc()).offset(skip).limit(limit).all()

    return {
        "payments": [
            {
                "payment_id": p.id,
                "store_id":   p.store_id,
                "store_name": p.store.name if p.store else None,
                "amount":     p.amount,
                "plan":       p.plan,
                "months":     p.months,
                "mpesa_ref":  p.mpesa_ref,
                "status":     p.status,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in payments
        ]
    }
