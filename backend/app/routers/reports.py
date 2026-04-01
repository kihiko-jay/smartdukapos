"""
Reports router — v4.0

Critical fixes:
  1. STORE ISOLATION: every query now filters by current.store_id.
     Previously all reports aggregated ALL shops' data together — a
     data leak where Shop A could see Shop B's revenue.
  2. PLATFORM_OWNER bypass: platform owner can pass ?store_id= to view
     any store's reports for support purposes. Shop users cannot.
  3. STORE NAME from DB record, not global config: Z-tape now shows
     the actual shop's name and location, not settings.STORE_NAME.
  4. VAT report converted to SQL aggregation (was loading full month
     into Python memory via .all()).
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, cast, Date, text
from typing import Optional
from datetime import date, timedelta

from app.core.deps import get_db, require_premium, get_current_employee
from app.database import business_date
from app.models.transaction import Transaction, TransactionItem, TransactionStatus
from app.models.product import Product
from app.models.employee import Employee, Role
from app.models.subscription import Store
from app.core.config import settings

router = APIRouter(prefix="/reports", tags=["Reports"])


# ── Helper: resolve which store_id to report on ───────────────────────────────

def _resolve_store(
    current: Employee,
    db:      Session,
    store_id_param: Optional[int] = None,
) -> tuple[int, Store]:
    """
    Returns (store_id, store_record) to use for this report.

    - Regular users always get their own store_id. The store_id_param
      is ignored — they cannot view another shop's reports.
    - PLATFORM_OWNER can pass ?store_id= to view any shop's data.
      If they don't pass it, defaults to the first store (for convenience).

    Raises 403 if the store is not found.
    """
    if current.role == Role.PLATFORM_OWNER:
        sid = store_id_param or current.store_id
        if not sid:
            # Platform owner with no store_id and no param — list stores instead
            raise Exception("PLATFORM_OWNER must supply ?store_id= to view reports")
    else:
        sid = current.store_id

    store = db.query(Store).filter(Store.id == sid).first()
    if not store:
        from fastapi import HTTPException
        raise HTTPException(404, f"Store {sid} not found")
    return sid, store


# ── Z-Tape / End of Day ───────────────────────────────────────────────────────

@router.get("/z-tape")
def z_tape(
    report_date:    Optional[date] = Query(default=None),
    store_id_param: Optional[int]  = Query(default=None, alias="store_id",
                                           description="Platform owner only"),
    db:      Session  = Depends(get_db),
    current: Employee = Depends(require_premium),
):
    """
    End-of-day Z-tape. Shows only this store's transactions.
    Store name and location come from the store's DB record, not global config.
    """
    from fastapi import HTTPException
    try:
        sid, store = _resolve_store(current, db, store_id_param)
    except Exception as e:
        raise HTTPException(400, str(e))

    target = report_date or business_date()

    totals_row = db.execute(
        text("""
            SELECT
                COUNT(*)                          AS transaction_count,
                COALESCE(SUM(total), 0)           AS gross_sales,
                COALESCE(SUM(discount_amount), 0) AS total_discounts,
                COALESCE(SUM(vat_amount), 0)      AS vat_collected
            FROM transactions
            WHERE DATE(created_at) = :target_date
              AND status           = 'completed'
              AND store_id         = :store_id
        """),
        {"target_date": target, "store_id": sid},
    ).fetchone()

    transaction_count = totals_row.transaction_count or 0
    gross_sales       = round(float(totals_row.gross_sales or 0), 2)
    total_discounts   = round(float(totals_row.total_discounts or 0), 2)
    vat_collected     = round(float(totals_row.vat_collected or 0), 2)
    net_sales_ex_vat  = round(gross_sales - vat_collected, 2)

    method_rows = db.execute(
        text("""
            SELECT
                payment_method,
                COUNT(*)   AS count,
                SUM(total) AS total
            FROM transactions
            WHERE DATE(created_at) = :target_date
              AND status           = 'completed'
              AND store_id         = :store_id
            GROUP BY payment_method
        """),
        {"target_date": target, "store_id": sid},
    ).fetchall()

    by_method = {
        row.payment_method: {"count": row.count, "total": round(float(row.total), 2)}
        for row in method_rows
    }

    cashier_rows = db.execute(
        text("""
            SELECT
                t.cashier_id,
                e.full_name        AS cashier_name,
                COUNT(*)           AS transaction_count,
                SUM(t.total)       AS total_sales
            FROM transactions t
            LEFT JOIN employees e ON e.id = t.cashier_id
            WHERE DATE(t.created_at) = :target_date
              AND t.status           = 'completed'
              AND t.store_id         = :store_id
            GROUP BY t.cashier_id, e.full_name
        """),
        {"target_date": target, "store_id": sid},
    ).fetchall()

    return {
        "report_type":       "Z-TAPE",
        # FIX: use store's own name/location, not global config
        "store_name":        store.name,
        "store_location":    store.location or "",
        "store_kra_pin":     store.kra_pin  or "",
        "date":              str(target),
        "currency":          settings.CURRENCY,
        "transaction_count": transaction_count,
        "gross_sales":       gross_sales,
        "total_discounts":   total_discounts,
        "net_sales_ex_vat":  net_sales_ex_vat,
        "vat_collected":     vat_collected,
        "vat_rate":          f"{int(settings.VAT_RATE * 100)}%",
        "by_payment_method": by_method,
        "cashier_breakdown": [
            {
                "cashier_id":        row.cashier_id,
                "cashier_name":      row.cashier_name or "Unknown",
                "transaction_count": row.transaction_count,
                "total_sales":       round(float(row.total_sales), 2),
            }
            for row in cashier_rows
        ],
    }


# ── Weekly Sales Summary ──────────────────────────────────────────────────────

@router.get("/weekly")
def weekly_summary(
    week_ending:    Optional[date] = Query(default=None),
    store_id_param: Optional[int]  = Query(default=None, alias="store_id"),
    db:      Session  = Depends(get_db),
    current: Employee = Depends(require_premium),
):
    from fastapi import HTTPException
    try:
        sid, store = _resolve_store(current, db, store_id_param)
    except Exception as e:
        raise HTTPException(400, str(e))

    end   = week_ending or business_date()
    start = end - timedelta(days=6)

    rows = db.execute(
        text("""
            SELECT
                DATE(created_at)                  AS day,
                COUNT(*)                          AS transaction_count,
                COALESCE(SUM(total), 0)           AS total_sales,
                COALESCE(SUM(vat_amount), 0)      AS vat_collected
            FROM transactions
            WHERE DATE(created_at) BETWEEN :start AND :end
              AND status   = 'completed'
              AND store_id = :store_id
            GROUP BY DATE(created_at)
            ORDER BY DATE(created_at) ASC
        """),
        {"start": start, "end": end, "store_id": sid},
    ).fetchall()

    by_date = {str(row.day): row for row in rows}

    daily = []
    for i in range(7):
        day     = start + timedelta(days=i)
        day_str = str(day)
        row     = by_date.get(day_str)
        daily.append({
            "date":              day_str,
            "day":               day.strftime("%a"),
            "transaction_count": row.transaction_count if row else 0,
            "total_sales":       round(float(row.total_sales),   2) if row else 0.0,
            "vat_collected":     round(float(row.vat_collected), 2) if row else 0.0,
        })

    week_total = sum(d["total_sales"]   for d in daily)
    week_vat   = sum(d["vat_collected"] for d in daily)

    return {
        "report_type":      "WEEKLY_SUMMARY",
        "store_name":       store.name,
        "period":           {"from": str(start), "to": str(end)},
        "currency":         settings.CURRENCY,
        "week_total_sales": round(week_total, 2),
        "week_total_vat":   round(week_vat, 2),
        "week_net_sales":   round(week_total - week_vat, 2),
        "daily_breakdown":  daily,
    }


# ── VAT Report (for KRA filing) ───────────────────────────────────────────────

@router.get("/vat")
def vat_report(
    month:          int = Query(..., ge=1, le=12),
    year:           int = Query(..., ge=2020),
    store_id_param: Optional[int] = Query(default=None, alias="store_id"),
    db:      Session  = Depends(get_db),
    current: Employee = Depends(require_premium),
):
    """
    Monthly VAT report for KRA filing.
    FIX: now uses SQL aggregation instead of loading all rows into Python.
    FIX: filtered to current store only.
    FIX: uses store's own KRA PIN, not global config.
    """
    from fastapi import HTTPException
    from calendar import monthrange
    try:
        sid, store = _resolve_store(current, db, store_id_param)
    except Exception as e:
        raise HTTPException(400, str(e))

    first_day = date(year, month, 1)
    last_day  = date(year, month, monthrange(year, month)[1])

    # FIX: SQL aggregation — no more .all() loading entire month into memory
    row = db.execute(
        text("""
            SELECT
                COUNT(*)                          AS transaction_count,
                COALESCE(SUM(total), 0)           AS total_gross,
                COALESCE(SUM(vat_amount), 0)      AS total_vat,
                COUNT(*) FILTER (WHERE etims_synced = TRUE) AS etims_count
            FROM transactions
            WHERE DATE(created_at) BETWEEN :first_day AND :last_day
              AND status   = 'completed'
              AND store_id = :store_id
        """),
        {"first_day": first_day, "last_day": last_day, "store_id": sid},
    ).fetchone()

    total_gross = round(float(row.total_gross or 0), 2)
    total_vat   = round(float(row.total_vat   or 0), 2)

    return {
        "report_type":         "VAT_MONTHLY",
        # FIX: store's own KRA PIN, not global settings.ETIMS_PIN
        "store_pin":           store.kra_pin  or settings.ETIMS_PIN,
        "store_name":          store.name,
        "period":              f"{first_day.strftime('%B %Y')}",
        "currency":            settings.CURRENCY,
        "vat_rate":            f"{int(settings.VAT_RATE * 100)}%",
        "total_gross_sales":   total_gross,
        "total_vat_collected": total_vat,
        "total_net_sales":     round(total_gross - total_vat, 2),
        "transaction_count":   row.transaction_count or 0,
        "etims_synced_count":  row.etims_count or 0,
    }


# ── Top Products ──────────────────────────────────────────────────────────────

@router.get("/top-products")
def top_products(
    report_date:    Optional[date] = Query(default=None),
    limit:          int = Query(default=10, le=50),
    store_id_param: Optional[int]  = Query(default=None, alias="store_id"),
    db:      Session  = Depends(get_db),
    current: Employee = Depends(require_premium),
):
    from fastapi import HTTPException
    try:
        sid, _ = _resolve_store(current, db, store_id_param)
    except Exception as e:
        raise HTTPException(400, str(e))

    target = report_date or business_date()

    rows = (
        db.query(
            TransactionItem.product_id,
            TransactionItem.product_name,
            TransactionItem.sku,
            func.sum(TransactionItem.qty).label("total_qty"),
            func.sum(TransactionItem.line_total).label("total_revenue"),
        )
        .join(Transaction, Transaction.id == TransactionItem.transaction_id)
        .filter(cast(Transaction.created_at, Date) == target)
        .filter(Transaction.status   == TransactionStatus.COMPLETED)
        # FIX: filter to this store only
        .filter(Transaction.store_id == sid)
        .group_by(
            TransactionItem.product_id,
            TransactionItem.product_name,
            TransactionItem.sku,
        )
        .order_by(func.sum(TransactionItem.line_total).desc())
        .limit(limit)
        .all()
    )

    return {
        "date": str(target),
        "products": [
            {
                "product_id":   r.product_id,
                "product_name": r.product_name,
                "sku":          r.sku,
                "units_sold":   int(r.total_qty),
                "revenue":      round(float(r.total_revenue), 2),
            }
            for r in rows
        ],
    }


# ── Low Stock Alert ───────────────────────────────────────────────────────────

@router.get("/low-stock")
def low_stock_report(
    store_id_param: Optional[int] = Query(default=None, alias="store_id"),
    db:      Session  = Depends(get_db),
    current: Employee = Depends(require_premium),
):
    from fastapi import HTTPException
    try:
        sid, _ = _resolve_store(current, db, store_id_param)
    except Exception as e:
        raise HTTPException(400, str(e))

    products = (
        db.query(Product)
        # FIX: filter to this store's products only
        .filter(Product.store_id      == sid)
        .filter(Product.is_active     == True)
        .filter(Product.stock_quantity <= Product.reorder_level)
        .order_by(Product.stock_quantity.asc())
        .all()
    )

    return {
        "report_type": "LOW_STOCK",
        "item_count":  len(products),
        "items": [
            {
                "product_id":          p.id,
                "sku":                 p.sku,
                "name":                p.name,
                "current_stock":       p.stock_quantity,
                "reorder_level":       p.reorder_level,
                "units_below_reorder": p.reorder_level - p.stock_quantity,
                "status":              "CRITICAL" if p.stock_quantity == 0 else "LOW",
            }
            for p in products
        ],
    }
