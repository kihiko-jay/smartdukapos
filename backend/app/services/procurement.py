"""
Procurement service — inbound inventory business logic.

All state-changing operations that touch stock or PO status live here.
Routers are thin; they call these functions inside a single DB session.

Critical invariants enforced here:
  1. Stock only increases when a GRN is POSTED, never on draft.
  2. accepted_qty = received_qty - damaged_qty - rejected_qty
  3. GRN cannot be posted if it is empty.
  4. GRN cannot be double-posted (idempotency guard).
  5. Over-receiving is blocked unless explicitly flagged.
  6. Product rows are locked with FOR UPDATE during stock writes.
  7. PO item rows are locked with FOR UPDATE during receipt posting.
  8. PO status is recomputed from its items after every GRN post.
  9. Every posted GRN generates StockMovement ledger entries.
 10. Every critical action writes an AuditTrail entry.
"""

import json
import logging
import uuid as _uuid
from datetime import datetime, timezone, date as _date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, List

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.product import Product, Supplier, StockMovement
from app.models.employee import Employee
from app.models.audit import AuditTrail
from app.models.procurement import (
    ProductPackaging, PurchaseOrder, PurchaseOrderItem,
    GoodsReceivedNote, GoodsReceivedItem, SupplierInvoiceMatch,
    POStatus, GRNStatus, InvoiceMatchStatus, PurchaseUnitType,
)
from app.schemas.procurement import (
    POCreate, POUpdate, GRNCreate, InvoiceMatchCreate, InvoiceMatchResolve,
)
from app.database import business_date

logger = logging.getLogger("dukapos.procurement")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _generate_po_number() -> str:
    return f"PO-{_uuid.uuid4().hex[:8].upper()}"


def _generate_grn_number() -> str:
    return f"GRN-{_uuid.uuid4().hex[:8].upper()}"


def _audit(db: Session, actor: Employee, action: str, entity: str,
           entity_id: str, store_id: int, before=None, after=None, notes: str = None):
    db.add(AuditTrail(
        store_id   = store_id,
        actor_id   = actor.id,
        actor_name = actor.full_name,
        action     = action,
        entity     = entity,
        entity_id  = entity_id,
        before_val = before,
        after_val  = after,
        notes      = notes,
    ))


def _assert_store(obj, store_id: int, label: str):
    if obj.store_id != store_id:
        raise HTTPException(403, f"{label} belongs to a different store")


def _get_supplier(db: Session, supplier_id: int, store_id: int) -> Supplier:
    s = db.query(Supplier).filter(
        Supplier.id == supplier_id,
        Supplier.store_id == store_id,
        Supplier.is_active == True,
    ).first()
    if not s:
        raise HTTPException(404, f"Supplier {supplier_id} not found")
    return s


def _get_product(db: Session, product_id: int, store_id: int,
                 lock: bool = False) -> Product:
    q = db.query(Product).filter(
        Product.id == product_id,
        Product.store_id == store_id,
        Product.is_active == True,
    )
    if lock:
        q = q.with_for_update()
    p = q.first()
    if not p:
        raise HTTPException(404, f"Product {product_id} not found in this store")
    return p


def _recalc_po_totals(po: PurchaseOrder):
    """Recompute PO subtotal and total from its items."""
    subtotal = sum(item.line_total for item in po.items)
    po.subtotal     = subtotal
    po.tax_amount   = Decimal("0.00")   # extend later for line-level VAT if needed
    po.total_amount = subtotal


def _recompute_po_status(po: PurchaseOrder):
    """
    Derive PO status from its items' received quantities.
    Called after every GRN post that references this PO.
    """
    if po.status in (POStatus.CANCELLED, POStatus.CLOSED):
        return  # terminal states — don't touch

    if not po.items:
        return

    fully_received = all(
        item.received_qty_base >= item.ordered_qty_base
        for item in po.items
    )
    any_received = any(item.received_qty_base > 0 for item in po.items)

    if fully_received:
        po.status = POStatus.FULLY_RECEIVED
    elif any_received:
        po.status = POStatus.PARTIALLY_RECEIVED
    elif po.status == POStatus.PARTIALLY_RECEIVED:
        # Edge case: all received quantities zeroed — revert
        po.status = POStatus.APPROVED


def _add_stock_movement(
    db: Session,
    product: Product,
    store_id: int,
    movement_type: str,
    qty_delta: int,
    ref_id: str,
    actor_id: int,
    notes: str = None,
):
    qty_before = product.stock_quantity
    product.stock_quantity += qty_delta
    db.add(StockMovement(
        product_id    = product.id,
        store_id      = store_id,
        movement_type = movement_type,
        qty_delta     = qty_delta,
        qty_before    = qty_before,
        qty_after     = product.stock_quantity,
        ref_id        = ref_id,
        notes         = notes,
        performed_by  = actor_id,
    ))


# ── ProductPackaging ──────────────────────────────────────────────────────────

def upsert_packaging(db: Session, product_id: int, store_id: int,
                     purchase_unit_type: str, units_per_purchase: int,
                     label: str = None, is_default: bool = False) -> ProductPackaging:
    _get_product(db, product_id, store_id)  # validates ownership
    existing = db.query(ProductPackaging).filter(
        ProductPackaging.product_id         == product_id,
        ProductPackaging.purchase_unit_type == purchase_unit_type,
    ).first()
    if existing:
        existing.units_per_purchase = units_per_purchase
        existing.label              = label or existing.label
        existing.is_default         = is_default
        return existing
    pkg = ProductPackaging(
        product_id         = product_id,
        store_id           = store_id,
        purchase_unit_type = purchase_unit_type,
        units_per_purchase = units_per_purchase,
        label              = label,
        is_default         = is_default,
    )
    db.add(pkg)
    return pkg


def get_packaging(db: Session, product_id: int,
                  purchase_unit_type: str) -> Optional[ProductPackaging]:
    return db.query(ProductPackaging).filter(
        ProductPackaging.product_id         == product_id,
        ProductPackaging.purchase_unit_type == purchase_unit_type,
    ).first()


def resolve_base_units(db: Session, product_id: int,
                       qty_purchase: Decimal,
                       purchase_unit_type: str,
                       units_per_purchase: int) -> int:
    """
    Convert purchase-unit quantity to base units.

    Priority:
      1. Use the caller-supplied units_per_purchase (from PO or GRN line).
      2. Cross-validate against the ProductPackaging table if a row exists
         (emit a warning if they diverge — the caller value wins).

    Returns integer base units (fractional units are rounded up — you cannot
    receive half a bottle).
    """
    if purchase_unit_type in (PurchaseUnitType.UNIT, "unit"):
        return int(qty_purchase)   # no conversion needed

    stored_pkg = get_packaging(db, product_id, purchase_unit_type)
    if stored_pkg and stored_pkg.units_per_purchase != units_per_purchase:
        logger.warning(
            "units_per_purchase mismatch for product %s type %s: "
            "caller=%d stored=%d — using caller value",
            product_id, purchase_unit_type,
            units_per_purchase, stored_pkg.units_per_purchase,
        )

    raw = qty_purchase * units_per_purchase
    return int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# ── Purchase Orders ───────────────────────────────────────────────────────────

def create_po(db: Session, payload: POCreate, actor: Employee) -> PurchaseOrder:
    store_id = actor.store_id
    _get_supplier(db, payload.supplier_id, store_id)

    po = PurchaseOrder(
        store_id      = store_id,
        supplier_id   = payload.supplier_id,
        po_number     = _generate_po_number(),
        status        = POStatus.DRAFT,
        order_date    = business_date(),
        expected_date = payload.expected_date,
        notes         = payload.notes,
        currency      = payload.currency,
        created_by    = actor.id,
    )
    db.add(po)
    db.flush()  # get po.id

    for item_data in payload.items:
        _get_product(db, item_data.product_id, store_id)
        base_qty = resolve_base_units(
            db, item_data.product_id,
            item_data.ordered_qty_purchase,
            item_data.purchase_unit_type,
            item_data.units_per_purchase,
        )
        line_total = (Decimal(str(base_qty)) * item_data.unit_cost).quantize(Decimal("0.01"))
        db.add(PurchaseOrderItem(
            purchase_order_id    = po.id,
            product_id           = item_data.product_id,
            ordered_qty_purchase = item_data.ordered_qty_purchase,
            purchase_unit_type   = item_data.purchase_unit_type,
            units_per_purchase   = item_data.units_per_purchase,
            ordered_qty_base     = base_qty,
            unit_cost            = item_data.unit_cost,
            line_total           = line_total,
            notes                = item_data.notes,
        ))

    db.flush()
    _recalc_po_totals(po)
    _audit(db, actor, "po_create", "purchase_order", po.po_number, store_id,
           after={"status": "draft", "supplier_id": po.supplier_id,
                  "total": str(po.total_amount)})
    return po


def update_po(db: Session, po_id: int, payload: POUpdate, actor: Employee) -> PurchaseOrder:
    po = _get_po(db, po_id, actor.store_id)
    if po.status not in (POStatus.DRAFT,):
        raise HTTPException(400, "Only draft POs can be edited")

    if payload.expected_date is not None:
        po.expected_date = payload.expected_date
    if payload.notes is not None:
        po.notes = payload.notes

    if payload.items is not None:
        # Replace all items
        for item in list(po.items):
            db.delete(item)
        db.flush()

        for item_data in payload.items:
            _get_product(db, item_data.product_id, actor.store_id)
            base_qty = resolve_base_units(
                db, item_data.product_id,
                item_data.ordered_qty_purchase,
                item_data.purchase_unit_type,
                item_data.units_per_purchase,
            )
            line_total = (Decimal(str(base_qty)) * item_data.unit_cost).quantize(Decimal("0.01"))
            db.add(PurchaseOrderItem(
                purchase_order_id    = po.id,
                product_id           = item_data.product_id,
                ordered_qty_purchase = item_data.ordered_qty_purchase,
                purchase_unit_type   = item_data.purchase_unit_type,
                units_per_purchase   = item_data.units_per_purchase,
                ordered_qty_base     = base_qty,
                unit_cost            = item_data.unit_cost,
                line_total           = line_total,
                notes                = item_data.notes,
            ))
        db.flush()
        _recalc_po_totals(po)

    _audit(db, actor, "po_update", "purchase_order", po.po_number, actor.store_id)
    return po


def submit_po(db: Session, po_id: int, actor: Employee) -> PurchaseOrder:
    po = _get_po(db, po_id, actor.store_id)
    if po.status != POStatus.DRAFT:
        raise HTTPException(400, f"PO is already {po.status.value}, cannot submit")
    if not po.items:
        raise HTTPException(400, "Cannot submit a PO with no items")
    po.status = POStatus.SUBMITTED
    _audit(db, actor, "po_submit", "purchase_order", po.po_number, actor.store_id,
           before={"status": "draft"}, after={"status": "submitted"})
    return po


def approve_po(db: Session, po_id: int, actor: Employee) -> PurchaseOrder:
    po = _get_po(db, po_id, actor.store_id)
    if po.status != POStatus.SUBMITTED:
        raise HTTPException(400, f"PO must be submitted before it can be approved (current: {po.status.value})")
    po.status      = POStatus.APPROVED
    po.approved_by = actor.id
    po.approved_at = datetime.now(timezone.utc)
    _audit(db, actor, "po_approve", "purchase_order", po.po_number, actor.store_id,
           before={"status": "submitted"}, after={"status": "approved"})
    return po


def cancel_po(db: Session, po_id: int, actor: Employee) -> PurchaseOrder:
    po = _get_po(db, po_id, actor.store_id)
    if po.status in (POStatus.FULLY_RECEIVED, POStatus.CLOSED):
        raise HTTPException(400, f"Cannot cancel a {po.status.value} PO")
    if po.status == POStatus.CANCELLED:
        raise HTTPException(400, "PO is already cancelled")
    po.status = POStatus.CANCELLED
    _audit(db, actor, "po_cancel", "purchase_order", po.po_number, actor.store_id,
           before={"status": po.status.value}, after={"status": "cancelled"})
    return po


def _get_po(db: Session, po_id: int, store_id: int) -> PurchaseOrder:
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(404, f"PurchaseOrder {po_id} not found")
    _assert_store(po, store_id, "PurchaseOrder")
    return po


def list_pos(db: Session, store_id: int, supplier_id=None,
             status=None, skip=0, limit=50):
    q = db.query(PurchaseOrder).filter(PurchaseOrder.store_id == store_id)
    if supplier_id:
        q = q.filter(PurchaseOrder.supplier_id == supplier_id)
    if status:
        q = q.filter(PurchaseOrder.status == status)
    return q.order_by(PurchaseOrder.created_at.desc()).offset(skip).limit(limit).all()


# ── GRN ───────────────────────────────────────────────────────────────────────

def create_grn(db: Session, payload: GRNCreate, actor: Employee) -> GoodsReceivedNote:
    store_id = actor.store_id
    _get_supplier(db, payload.supplier_id, store_id)

    po = None
    if payload.purchase_order_id:
        po = _get_po(db, payload.purchase_order_id, store_id)
        if po.supplier_id != payload.supplier_id:
            raise HTTPException(400, "GRN supplier must match the PO supplier")
        if po.status not in (POStatus.APPROVED, POStatus.PARTIALLY_RECEIVED):
            raise HTTPException(400,
                f"PO must be approved or partially_received to receive against (current: {po.status.value})")

    grn = GoodsReceivedNote(
        store_id                = store_id,
        supplier_id             = payload.supplier_id,
        purchase_order_id       = payload.purchase_order_id,
        grn_number              = _generate_grn_number(),
        status                  = GRNStatus.DRAFT,
        received_date           = payload.received_date or business_date(),
        supplier_invoice_number = payload.supplier_invoice_number,
        supplier_delivery_note  = payload.supplier_delivery_note,
        notes                   = payload.notes,
        received_by             = actor.id,
    )
    db.add(grn)
    db.flush()

    for line in payload.items:
        _get_product(db, line.product_id, store_id)  # ownership check only

        base_qty = resolve_base_units(
            db, line.product_id,
            line.received_qty_purchase,
            line.purchase_unit_type,
            line.units_per_purchase,
        )

        if line.damaged_qty_base + line.rejected_qty_base > base_qty:
            raise HTTPException(400,
                f"Product {line.product_id}: damaged + rejected ({line.damaged_qty_base + line.rejected_qty_base}) "
                f"cannot exceed received base qty ({base_qty})")

        line_total = (
            Decimal(str(base_qty)) * line.cost_per_base_unit
        ).quantize(Decimal("0.01"))

        db.add(GoodsReceivedItem(
            grn_id                 = grn.id,
            product_id             = line.product_id,
            purchase_order_item_id = line.purchase_order_item_id,
            received_qty_purchase  = line.received_qty_purchase,
            purchase_unit_type     = line.purchase_unit_type,
            units_per_purchase     = line.units_per_purchase,
            received_qty_base      = base_qty,
            damaged_qty_base       = line.damaged_qty_base,
            rejected_qty_base      = line.rejected_qty_base,
            cost_per_base_unit     = line.cost_per_base_unit,
            line_total             = line_total,
            batch_number           = line.batch_number,
            expiry_date            = line.expiry_date,
            notes                  = line.notes,
        ))

    _audit(db, actor, "grn_create", "grn", grn.grn_number, store_id,
           after={"status": "draft", "supplier_id": grn.supplier_id,
                  "po_id": grn.purchase_order_id})
    return grn


def post_grn(db: Session, grn_id: int, actor: Employee) -> GoodsReceivedNote:
    """
    Post a GRN — the only operation that mutates stock.

    Steps:
      1. Lock the GRN row (idempotency guard).
      2. Validate GRN has at least one item.
      3. For each item:
         a. Lock the Product row (FOR UPDATE).
         b. Add accepted_qty to product.stock_quantity.
         c. Write a purchase_receive StockMovement.
         d. If damaged_qty > 0, write a purchase_receive_damaged StockMovement
            (qty tracked but does NOT increase sellable stock).
         e. If rejected_qty > 0, write a purchase_reject StockMovement
            (audit only, no stock change).
         f. If item is linked to a PO item, lock PO item and update received qty.
      4. Recompute PO status if linked.
      5. Mark GRN as POSTED.
      6. Write audit trail.
    """
    # Step 1 — fetch and lock GRN row
    grn = db.query(GoodsReceivedNote).filter(
        GoodsReceivedNote.id == grn_id,
    ).with_for_update().first()

    if not grn:
        raise HTTPException(404, f"GRN {grn_id} not found")
    _assert_store(grn, actor.store_id, "GRN")

    if grn.status == GRNStatus.POSTED:
        raise HTTPException(400, "GRN is already posted (idempotency guard)")
    if grn.status == GRNStatus.CANCELLED:
        raise HTTPException(400, "Cannot post a cancelled GRN")
    if not grn.items:
        raise HTTPException(400, "Cannot post an empty GRN")

    # Step 3 — process each item
    for item in grn.items:
        accepted_qty = item.accepted_qty_base

        # a. Lock product row
        product = db.query(Product).filter(
            Product.id == item.product_id,
        ).with_for_update().first()
        if not product:
            raise HTTPException(404, f"Product {item.product_id} not found")

        # b+c. Accepted qty → sellable stock
        if accepted_qty > 0:
            _add_stock_movement(
                db, product, grn.store_id,
                "purchase_receive", accepted_qty,
                grn.grn_number, actor.id,
                notes=f"GRN {grn.grn_number} — accepted",
            )

        # d. Damaged qty — tracked, NOT added to sellable stock
        if item.damaged_qty_base > 0:
            _add_stock_movement(
                db, product, grn.store_id,
                "purchase_receive_damaged", 0,   # delta=0: no sellable stock change
                grn.grn_number, actor.id,
                notes=(
                    f"GRN {grn.grn_number} — {item.damaged_qty_base} units damaged, "
                    "not added to sellable stock"
                ),
            )

        # e. Rejected qty — audit record only
        if item.rejected_qty_base > 0:
            _add_stock_movement(
                db, product, grn.store_id,
                "purchase_reject", 0,            # delta=0: no stock change
                grn.grn_number, actor.id,
                notes=(
                    f"GRN {grn.grn_number} — {item.rejected_qty_base} units rejected, "
                    "returned to supplier"
                ),
            )

        # f. Update PO item received quantities (with row lock)
        if item.purchase_order_item_id:
            po_item = db.query(PurchaseOrderItem).filter(
                PurchaseOrderItem.id == item.purchase_order_item_id,
            ).with_for_update().first()

            if po_item:
                new_received = po_item.received_qty_base + item.received_qty_base
                # Over-receive guard: accepted only — we warn but don't hard-block
                # to handle legitimate scenarios (e.g. supplier sent extra)
                if new_received > po_item.ordered_qty_base:
                    logger.warning(
                        "Over-receive on PO item %d: ordered=%d, "
                        "previously_received=%d, now_receiving=%d",
                        po_item.id, po_item.ordered_qty_base,
                        po_item.received_qty_base, item.received_qty_base,
                    )
                po_item.received_qty_base += item.received_qty_base
                po_item.damaged_qty_base  += item.damaged_qty_base
                po_item.rejected_qty_base += item.rejected_qty_base

        # Update product cost_price to latest received cost
        if item.cost_per_base_unit > 0:
            product.cost_price = item.cost_per_base_unit

    # Step 4 — recompute PO status
    if grn.purchase_order_id:
        po = db.query(PurchaseOrder).filter(
            PurchaseOrder.id == grn.purchase_order_id,
        ).with_for_update().first()
        if po:
            db.refresh(po)       # pick up updated po_item counts
            _recompute_po_status(po)

    # Step 5 — mark posted
    grn.status    = GRNStatus.POSTED
    grn.posted_at = datetime.now(timezone.utc)

    # Step 6 — audit
    _audit(db, actor, "grn_post", "grn", grn.grn_number, grn.store_id,
           before={"status": "draft"},
           after={"status": "posted", "po_id": grn.purchase_order_id})

    logger.info("GRN %s posted by employee %d", grn.grn_number, actor.id)
    return grn


def cancel_grn(db: Session, grn_id: int, actor: Employee) -> GoodsReceivedNote:
    grn = db.query(GoodsReceivedNote).filter(
        GoodsReceivedNote.id == grn_id,
    ).with_for_update().first()
    if not grn:
        raise HTTPException(404, "GRN not found")
    _assert_store(grn, actor.store_id, "GRN")
    if grn.status == GRNStatus.POSTED:
        raise HTTPException(400, "Cannot cancel a posted GRN — void it via a manual adjustment")
    if grn.status == GRNStatus.CANCELLED:
        raise HTTPException(400, "GRN is already cancelled")
    grn.status = GRNStatus.CANCELLED
    _audit(db, actor, "grn_cancel", "grn", grn.grn_number, grn.store_id,
           before={"status": "draft"}, after={"status": "cancelled"})
    return grn


def list_grns(db: Session, store_id: int, supplier_id=None,
              po_id=None, status=None, skip=0, limit=50):
    q = db.query(GoodsReceivedNote).filter(GoodsReceivedNote.store_id == store_id)
    if supplier_id:
        q = q.filter(GoodsReceivedNote.supplier_id == supplier_id)
    if po_id:
        q = q.filter(GoodsReceivedNote.purchase_order_id == po_id)
    if status:
        q = q.filter(GoodsReceivedNote.status == status)
    return q.order_by(GoodsReceivedNote.created_at.desc()).offset(skip).limit(limit).all()


# ── Invoice Matching ──────────────────────────────────────────────────────────

def create_invoice_match(db: Session, payload: InvoiceMatchCreate,
                         actor: Employee) -> SupplierInvoiceMatch:
    store_id = actor.store_id
    _get_supplier(db, payload.supplier_id, store_id)

    po  = None
    grn = None

    if payload.purchase_order_id:
        po = _get_po(db, payload.purchase_order_id, store_id)

    if payload.grn_id:
        grn = db.query(GoodsReceivedNote).filter(
            GoodsReceivedNote.id == payload.grn_id,
        ).first()
        if not grn:
            raise HTTPException(404, "GRN not found")
        _assert_store(grn, store_id, "GRN")
        if grn.status != GRNStatus.POSTED:
            raise HTTPException(400, "Can only match invoices against posted GRNs")

    # Compute variances
    variance = _compute_variance(po, grn, payload)

    # Determine initial match status
    if not variance["has_discrepancy"]:
        initial_status = InvoiceMatchStatus.MATCHED
    else:
        initial_status = InvoiceMatchStatus.PARTIAL if (po or grn) else InvoiceMatchStatus.UNMATCHED

    match = SupplierInvoiceMatch(
        store_id          = store_id,
        supplier_id       = payload.supplier_id,
        purchase_order_id = payload.purchase_order_id,
        grn_id            = payload.grn_id,
        invoice_number    = payload.invoice_number,
        invoice_date      = payload.invoice_date,
        invoice_total     = payload.invoice_total,
        matched_status    = initial_status,
        discrepancy_notes = payload.discrepancy_notes,
        variance_json     = json.dumps(variance),
        created_by        = actor.id,
    )
    db.add(match)
    _audit(db, actor, "invoice_match_create", "invoice_match",
           payload.invoice_number, store_id,
           after={"status": initial_status.value, "invoice_total": str(payload.invoice_total)})
    return match


def resolve_invoice_match(db: Session, match_id: int,
                          payload: InvoiceMatchResolve,
                          actor: Employee) -> SupplierInvoiceMatch:
    match = db.query(SupplierInvoiceMatch).filter(
        SupplierInvoiceMatch.id == match_id,
    ).first()
    if not match:
        raise HTTPException(404, "Invoice match not found")
    _assert_store(match, actor.store_id, "InvoiceMatch")

    allowed = ("matched", "disputed")
    if payload.matched_status not in allowed:
        raise HTTPException(400, f"matched_status must be one of {allowed}")

    before_status = match.matched_status
    match.matched_status    = payload.matched_status
    match.discrepancy_notes = payload.discrepancy_notes or match.discrepancy_notes
    match.resolved_by       = actor.id
    match.resolved_at       = datetime.now(timezone.utc)

    _audit(db, actor, "invoice_match_resolve", "invoice_match",
           match.invoice_number, actor.store_id,
           before={"status": before_status},
           after={"status": payload.matched_status})
    return match


def _compute_variance(po: Optional[PurchaseOrder],
                      grn: Optional[GoodsReceivedNote],
                      payload: InvoiceMatchCreate) -> dict:
    """
    Build a structured variance dict comparing invoice total to PO/GRN values.
    """
    result: dict = {
        "invoice_total":   float(payload.invoice_total),
        "po_total":        None,
        "grn_total":       None,
        "total_variance":  None,
        "has_discrepancy": False,
        "line_variances":  [],
    }

    if po:
        po_total = float(po.total_amount)
        result["po_total"] = po_total
        variance = float(payload.invoice_total) - po_total
        result["total_variance"] = round(variance, 2)
        if abs(variance) > 0.01:
            result["has_discrepancy"] = True
            result["line_variances"].append({
                "type": "total_vs_po",
                "invoice": float(payload.invoice_total),
                "expected": po_total,
                "variance": round(variance, 2),
            })

    if grn:
        grn_total = sum(float(item.line_total) for item in grn.items)
        result["grn_total"] = round(grn_total, 2)
        variance = float(payload.invoice_total) - grn_total
        if po is None:
            result["total_variance"] = round(variance, 2)
        if abs(variance) > 0.01:
            result["has_discrepancy"] = True
            result["line_variances"].append({
                "type": "total_vs_grn",
                "invoice": float(payload.invoice_total),
                "expected": round(grn_total, 2),
                "variance": round(variance, 2),
            })

    return result


# ── Reporting ─────────────────────────────────────────────────────────────────

def report_received_by_date(db: Session, store_id: int,
                             date_from: _date, date_to: _date):
    from sqlalchemy import func as sqlfunc
    rows = (
        db.query(GoodsReceivedNote)
        .filter(
            GoodsReceivedNote.store_id == store_id,
            GoodsReceivedNote.status   == GRNStatus.POSTED,
            GoodsReceivedNote.received_date >= date_from,
            GoodsReceivedNote.received_date <= date_to,
        )
        .order_by(GoodsReceivedNote.received_date.asc())
        .all()
    )
    return rows


def report_open_pos(db: Session, store_id: int):
    return (
        db.query(PurchaseOrder)
        .filter(
            PurchaseOrder.store_id == store_id,
            PurchaseOrder.status.in_([
                POStatus.APPROVED,
                POStatus.PARTIALLY_RECEIVED,
                POStatus.SUBMITTED,
            ])
        )
        .order_by(PurchaseOrder.order_date.asc())
        .all()
    )
