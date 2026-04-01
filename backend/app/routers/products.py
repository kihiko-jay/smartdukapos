"""
Products router — v4.0

Critical fixes:
  1. STORE ISOLATION: list_products, get_product, get_by_barcode, stock_history
     all now filter by current.store_id. Previously any authenticated user
     could see products from any other shop.
  2. PRODUCT CREATION: store_id is set from current.store_id (was missing).
  3. PRODUCT UPDATE/STOCK ADJUST: ownership check added — can only modify
     products that belong to your own store.
  4. CATEGORIES: filtered per store.
  5. SUPPLIERS: filtered per store.
  6. CACHE KEYS: now include store_id so Shop A never sees Shop B's cache.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List, Optional
from decimal import Decimal
from datetime import datetime

from app.core.deps import (
    get_db, require_cashier, require_premium, get_current_employee
)
from app.core.cache import (
    cache, product_list_key, product_detail_key, product_barcode_key,
    PRODUCT_LIST_TTL, PRODUCT_DETAIL_TTL, BARCODE_LOOKUP_TTL,
)
from app.models.product import Product, Category, Supplier, StockMovement
from app.models.employee import Employee, Role
from app.models.audit import AuditTrail
from app.schemas.product import (
    ProductCreate, ProductUpdate, ProductOut,
    CategoryCreate, CategoryOut,
    SupplierCreate, SupplierOut,
    StockAdjustment, StockMovementOut,
)

logger = logging.getLogger("dukapos.products")
router = APIRouter(prefix="/products", tags=["Products"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_audit(db, actor: Employee, action: str, entity_id: str,
                 before=None, after=None, notes=None):
    db.add(AuditTrail(
        store_id   = actor.store_id,
        actor_id   = actor.id,
        actor_name = actor.full_name,
        action     = action,
        entity     = "product",
        entity_id  = str(entity_id),
        before_val = before,
        after_val  = after,
        notes      = notes,
    ))


def _apply_stock_movement(db, product: Product, delta: int, movement_type: str,
                          store_id: int = None, ref_id: str = None,
                          notes: str = None, performed_by: int = None):
    qty_before = product.stock_quantity
    product.stock_quantity = max(0, product.stock_quantity + delta)
    qty_after  = product.stock_quantity
    movement   = StockMovement(
        product_id    = product.id,
        store_id      = store_id,
        movement_type = movement_type,
        qty_delta     = delta,
        qty_before    = qty_before,
        qty_after     = qty_after,
        ref_id        = ref_id,
        notes         = notes,
        performed_by  = performed_by,
    )
    db.add(movement)
    return movement


def _own_product(product: Product, current: Employee) -> bool:
    """True if this product belongs to the employee's store (or they are platform owner)."""
    if current.role == Role.PLATFORM_OWNER:
        return True
    return product.store_id == current.store_id


# ── Categories ────────────────────────────────────────────────────────────────

@router.get("/categories", response_model=List[CategoryOut])
def list_categories(
    db:      Session  = Depends(get_db),
    current: Employee = Depends(require_cashier),
):
    # FIX: filter to this store's categories only
    q = db.query(Category)
    if current.role != Role.PLATFORM_OWNER:
        q = q.filter(Category.store_id == current.store_id)
    return q.all()


@router.post("/categories", response_model=CategoryOut)
def create_category(
    payload: CategoryCreate,
    db:      Session  = Depends(get_db),
    current: Employee = Depends(require_premium),
):
    cat = Category(**payload.model_dump(), store_id=current.store_id)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


# ── Suppliers ─────────────────────────────────────────────────────────────────

@router.get("/suppliers", response_model=List[SupplierOut])
def list_suppliers(
    db:      Session  = Depends(get_db),
    current: Employee = Depends(require_premium),
):
    q = db.query(Supplier).filter(Supplier.is_active == True)
    if current.role != Role.PLATFORM_OWNER:
        # FIX: filter to this store's suppliers only
        q = q.filter(Supplier.store_id == current.store_id)
    return q.all()


@router.post("/suppliers", response_model=SupplierOut)
def create_supplier(
    payload: SupplierCreate,
    db:      Session  = Depends(get_db),
    current: Employee = Depends(require_premium),
):
    s = Supplier(**payload.model_dump(), store_id=current.store_id)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


# ── Products ──────────────────────────────────────────────────────────────────

@router.get("", response_model=List[ProductOut])
async def list_products(
    search:      Optional[str]  = Query(None),
    category_id: Optional[int]  = None,
    supplier_id: Optional[int]  = None,
    low_stock:   Optional[bool] = None,
    is_active:   bool = True,
    skip:        int  = 0,
    limit:       int  = 100,
    db:      Session  = Depends(get_db),
    current: Employee = Depends(require_cashier),
):
    sid = current.store_id

    # Cache only simple unfiltered reads, keyed per store
    use_cache = (
        search is None and category_id is None and supplier_id is None
        and low_stock is None and is_active is True and skip == 0 and limit == 100
        and current.role != Role.PLATFORM_OWNER
    )
    # FIX: cache key includes store_id — Shop A never sees Shop B's cache
    cache_key = product_list_key(store_id=sid, is_active=is_active)

    if use_cache:
        cached = await cache.get(cache_key)
        if cached is not None:
            return cached

    q = db.query(Product).filter(Product.is_active == is_active)

    # FIX: always filter by store — platform owner can see all, others see only theirs
    if current.role != Role.PLATFORM_OWNER:
        q = q.filter(Product.store_id == sid)

    if search:
        like = f"%{search}%"
        q = q.filter(or_(
            Product.name.ilike(like),
            Product.sku.ilike(like),
            Product.barcode.ilike(like),
        ))
    if category_id:
        q = q.filter(Product.category_id == category_id)
    if supplier_id:
        q = q.filter(Product.supplier_id == supplier_id)
    if low_stock:
        q = q.filter(Product.stock_quantity <= Product.reorder_level)

    results = q.offset(skip).limit(limit).all()

    if use_cache:
        serialized = [ProductOut.model_validate(r).model_dump(mode="json") for r in results]
        await cache.set(cache_key, serialized, ttl=PRODUCT_LIST_TTL)

    return results


@router.get("/{product_id}", response_model=ProductOut)
def get_product(
    product_id: int,
    db:      Session  = Depends(get_db),
    current: Employee = Depends(require_cashier),
):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        raise HTTPException(404, "Product not found")
    # FIX: ownership check
    if not _own_product(p, current):
        raise HTTPException(403, "Product not found in your store")
    return p


@router.get("/barcode/{barcode}", response_model=ProductOut)
async def get_by_barcode(
    barcode: str,
    db:      Session  = Depends(get_db),
    current: Employee = Depends(require_cashier),
):
    # FIX: cache key includes store_id
    cache_key = product_barcode_key(f"{current.store_id}:{barcode}")
    cached = await cache.get(cache_key)
    if cached is not None:
        return cached

    q = db.query(Product).filter(Product.barcode == barcode)
    if current.role != Role.PLATFORM_OWNER:
        # FIX: only find barcode within this store
        q = q.filter(Product.store_id == current.store_id)

    p = q.first()
    if not p:
        raise HTTPException(404, f"No product found for barcode: {barcode}")

    serialized = ProductOut.model_validate(p).model_dump(mode="json")
    await cache.set(cache_key, serialized, ttl=BARCODE_LOOKUP_TTL)
    return p


@router.post("", response_model=ProductOut)
async def create_product(
    payload: ProductCreate,
    db:      Session  = Depends(get_db),
    current: Employee = Depends(require_premium),
):
    # FIX: SKU uniqueness within this store only (not globally)
    existing = (
        db.query(Product)
        .filter(Product.sku == payload.sku)
        .filter(Product.store_id == current.store_id)
        .first()
    )
    if existing:
        raise HTTPException(400, f"SKU '{payload.sku}' already exists in your store")

    data        = payload.model_dump()
    initial_qty = data.pop("stock_quantity", 0)
    # FIX: set store_id from authenticated employee
    p = Product(**data, stock_quantity=0, store_id=current.store_id)
    db.add(p)
    db.flush()

    if initial_qty > 0:
        _apply_stock_movement(db, p, initial_qty, "purchase",
                              store_id=current.store_id,
                              notes="Initial stock on product creation",
                              performed_by=current.id)

    _write_audit(db, current, "create", p.sku, after={"sku": p.sku, "name": p.name})
    db.commit()
    db.refresh(p)
    await cache.invalidate_prefix(f"products:list:{current.store_id}")
    return p


@router.patch("/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: int,
    payload:    ProductUpdate,
    db:      Session  = Depends(get_db),
    current: Employee = Depends(require_premium),
):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        raise HTTPException(404, "Product not found")
    # FIX: ownership check
    if not _own_product(p, current):
        raise HTTPException(403, "Product not found in your store")

    before = {"name": p.name, "selling_price": str(p.selling_price)}
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(p, field, value)

    _write_audit(db, current, "update", p.sku, before=before,
                 after={"name": p.name, "selling_price": str(p.selling_price)})
    db.commit()
    db.refresh(p)
    await cache.invalidate_prefix(f"products:list:{current.store_id}")
    return p


# ── Stock ─────────────────────────────────────────────────────────────────────

@router.post("/stock/adjust")
async def adjust_stock(
    payload: StockAdjustment,
    db:      Session  = Depends(get_db),
    current: Employee = Depends(require_premium),
):
    p = db.query(Product).filter(Product.id == payload.product_id).first()
    if not p:
        raise HTTPException(404, "Product not found")
    # FIX: ownership check
    if not _own_product(p, current):
        raise HTTPException(403, "Product not found in your store")

    movement = _apply_stock_movement(
        db, p, payload.quantity_change,
        movement_type = payload.reason,
        store_id      = current.store_id,
        notes         = payload.notes,
        performed_by  = current.id,
    )
    _write_audit(db, current, "stock_adj", p.sku,
                 before={"stock": movement.qty_before},
                 after={"stock":  movement.qty_after},
                 notes=f"{payload.reason}: {payload.quantity_change}")
    db.commit()
    await cache.invalidate_prefix(f"products:list:{current.store_id}")
    return {
        "product_id":  p.id,
        "sku":         p.sku,
        "new_stock":   p.stock_quantity,
        "adjustment":  payload.quantity_change,
        "reason":      payload.reason,
        "movement_id": movement.id,
    }


@router.get("/{product_id}/stock-history")
def stock_history(
    product_id: int,
    limit: int = Query(default=50, le=200),
    since: Optional[str] = Query(default=None),
    db:      Session  = Depends(get_db),
    current: Employee = Depends(require_premium),
):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        raise HTTPException(404, "Product not found")
    # FIX: ownership check
    if not _own_product(p, current):
        raise HTTPException(403, "Product not found in your store")

    q = db.query(StockMovement).filter(StockMovement.product_id == product_id)

    if since is not None:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(400, "Invalid since datetime — use ISO 8601 format")
        q = q.filter(StockMovement.created_at > since_dt)

    q = q.order_by(StockMovement.created_at.desc())
    total_movements = q.count()
    movements       = q.limit(limit).all()

    return {
        "product_id":      p.id,
        "product_name":    p.name,
        "sku":             p.sku,
        "movements": [
            {
                "id":                m.id,
                "movement_type":     m.movement_type,
                "qty_delta":         m.qty_delta,
                "qty_before":        m.qty_before,
                "qty_after":         m.qty_after,
                "ref_id":            m.ref_id,
                "notes":             m.notes,
                "performed_by_name": m.employee.full_name if m.employee else None,
                "created_at":        m.created_at.isoformat() if m.created_at else None,
            }
            for m in movements
        ],
        "total_movements": total_movements,
    }
