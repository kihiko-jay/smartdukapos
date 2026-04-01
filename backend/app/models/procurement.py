"""
Procurement models — inbound inventory workflow.

Entities:
  ProductPackaging      — per-product purchase unit definitions (carton/pack/etc)
  PurchaseOrder         — a request to a supplier for goods
  PurchaseOrderItem     — one product line on a PO
  GoodsReceivedNote     — a receiving event (may be partial) against a PO or direct
  GoodsReceivedItem     — one product line on a GRN
  SupplierInvoiceMatch  — links supplier invoice to PO + GRN, tracks discrepancies

Design rules:
  - All records carry store_id; cross-store access is blocked at the router.
  - Stock is NEVER updated by PO creation. Only a POSTED GRN touches stock.
  - accepted_qty = received_qty - damaged_qty - rejected_qty
  - PO status machine: draft → submitted → approved → partially_received
                        → fully_received → closed | cancelled
  - GRN status machine: draft → posted | cancelled
"""

import enum
import uuid as _uuid

from sqlalchemy import (
    Column, Integer, String, Numeric, Boolean, DateTime,
    Date, Enum, ForeignKey, Text, CheckConstraint, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


# ── Enums ─────────────────────────────────────────────────────────────────────

class POStatus(str, enum.Enum):
    DRAFT              = "draft"
    SUBMITTED          = "submitted"
    APPROVED           = "approved"
    PARTIALLY_RECEIVED = "partially_received"
    FULLY_RECEIVED     = "fully_received"
    CLOSED             = "closed"
    CANCELLED          = "cancelled"


class GRNStatus(str, enum.Enum):
    DRAFT     = "draft"
    POSTED    = "posted"
    CANCELLED = "cancelled"


class InvoiceMatchStatus(str, enum.Enum):
    UNMATCHED = "unmatched"
    PARTIAL   = "partial"
    MATCHED   = "matched"
    DISPUTED  = "disputed"


class PurchaseUnitType(str, enum.Enum):
    UNIT   = "unit"
    PACK   = "pack"
    BOX    = "box"
    CARTON = "carton"
    CASE   = "case"
    DOZEN  = "dozen"
    BALE   = "bale"
    SACK   = "sack"
    ROLL   = "roll"
    OTHER  = "other"


# ── ProductPackaging ──────────────────────────────────────────────────────────

class ProductPackaging(Base):
    """
    Defines how many base units make up each purchase unit for a product.

    Example: Coke 500ml  →  carton contains 24 bottles
      product_id           = <coke product id>
      purchase_unit_type   = "carton"
      units_per_purchase   = 24
      label                = "Carton (24 bottles)"

    A product can have multiple packaging rows (e.g. both carton and pack).
    The 'unit' (1:1) row is the base unit and is always implicit, but may
    be stored for UI completeness.
    """
    __tablename__ = "product_packaging"
    __table_args__ = (
        UniqueConstraint("product_id", "purchase_unit_type",
                         name="uq_packaging_product_unit"),
        CheckConstraint("units_per_purchase > 0",
                        name="ck_packaging_units_positive"),
    )

    id                  = Column(Integer, primary_key=True, index=True)
    product_id          = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    store_id            = Column(Integer, ForeignKey("stores.id"),   nullable=False, index=True)
    purchase_unit_type  = Column(Enum(PurchaseUnitType), nullable=False)
    units_per_purchase  = Column(Integer, nullable=False, default=1)
    label               = Column(String(100), nullable=True)   # e.g. "Carton (24 bottles)"
    is_default          = Column(Boolean, default=False)       # default purchase unit
    created_at          = Column(DateTime(timezone=True), server_default=func.now())

    product = relationship("Product", back_populates="packaging")


# ── PurchaseOrder ─────────────────────────────────────────────────────────────

class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id             = Column(Integer, primary_key=True, index=True)
    store_id       = Column(Integer, ForeignKey("stores.id"),    nullable=False, index=True)
    supplier_id    = Column(Integer, ForeignKey("suppliers.id"), nullable=False, index=True)
    po_number      = Column(String(30), nullable=False, unique=True, index=True)
    status         = Column(Enum(POStatus), nullable=False, default=POStatus.DRAFT, index=True)

    order_date     = Column(Date, nullable=False, server_default=func.current_date())
    expected_date  = Column(Date, nullable=True)

    notes          = Column(Text, nullable=True)
    currency       = Column(String(3), nullable=False, default="KES")

    subtotal       = Column(Numeric(12, 2), default=0)
    tax_amount     = Column(Numeric(12, 2), default=0)
    total_amount   = Column(Numeric(12, 2), default=0)

    created_by     = Column(Integer, ForeignKey("employees.id"), nullable=False)
    approved_by    = Column(Integer, ForeignKey("employees.id"), nullable=True)
    approved_at    = Column(DateTime(timezone=True), nullable=True)

    created_at     = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at     = Column(DateTime(timezone=True), onupdate=func.now())

    supplier       = relationship("Supplier",  foreign_keys=[supplier_id])
    creator        = relationship("Employee",  foreign_keys=[created_by])
    approver       = relationship("Employee",  foreign_keys=[approved_by])
    items          = relationship("PurchaseOrderItem", back_populates="purchase_order",
                                  cascade="all, delete-orphan")
    grns           = relationship("GoodsReceivedNote", back_populates="purchase_order")


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    __table_args__ = (
        CheckConstraint("ordered_qty_purchase > 0",    name="ck_poi_ordered_qty_positive"),
        CheckConstraint("units_per_purchase > 0",       name="ck_poi_units_per_purchase_positive"),
        CheckConstraint("unit_cost >= 0",               name="ck_poi_unit_cost_non_negative"),
    )

    id                      = Column(Integer, primary_key=True, index=True)
    purchase_order_id       = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False, index=True)
    product_id              = Column(Integer, ForeignKey("products.id"),         nullable=False)

    # What was ordered
    ordered_qty_purchase    = Column(Numeric(10, 3), nullable=False)   # in purchase units (e.g. 10 cartons)
    purchase_unit_type      = Column(Enum(PurchaseUnitType), nullable=False, default=PurchaseUnitType.UNIT)
    units_per_purchase      = Column(Integer, nullable=False, default=1)  # e.g. 24 bottles per carton
    ordered_qty_base        = Column(Integer, nullable=False)              # computed: ordered_purchase * units_per_purchase

    unit_cost               = Column(Numeric(12, 2), nullable=False, default=0)   # cost per BASE unit
    line_total              = Column(Numeric(12, 2), nullable=False, default=0)   # ordered_qty_base * unit_cost

    # Running totals updated as GRNs are posted
    received_qty_base       = Column(Integer, nullable=False, default=0)
    damaged_qty_base        = Column(Integer, nullable=False, default=0)
    rejected_qty_base       = Column(Integer, nullable=False, default=0)

    notes                   = Column(Text, nullable=True)

    purchase_order = relationship("PurchaseOrder",  back_populates="items")
    product        = relationship("Product",         foreign_keys=[product_id])
    grn_items      = relationship("GoodsReceivedItem", back_populates="po_item")

    @property
    def remaining_qty_base(self) -> int:
        return max(0, self.ordered_qty_base - self.received_qty_base)

    @property
    def accepted_qty_base(self) -> int:
        return max(0, self.received_qty_base - self.damaged_qty_base - self.rejected_qty_base)


# ── GoodsReceivedNote ─────────────────────────────────────────────────────────

class GoodsReceivedNote(Base):
    __tablename__ = "goods_received_notes"

    id                      = Column(Integer, primary_key=True, index=True)
    store_id                = Column(Integer, ForeignKey("stores.id"),          nullable=False, index=True)
    supplier_id             = Column(Integer, ForeignKey("suppliers.id"),        nullable=False, index=True)
    purchase_order_id       = Column(Integer, ForeignKey("purchase_orders.id"), nullable=True,  index=True)

    grn_number              = Column(String(30), nullable=False, unique=True, index=True)
    status                  = Column(Enum(GRNStatus), nullable=False, default=GRNStatus.DRAFT, index=True)
    received_date           = Column(Date, nullable=False, server_default=func.current_date())

    supplier_invoice_number = Column(String(100), nullable=True)
    supplier_delivery_note  = Column(String(100), nullable=True)
    notes                   = Column(Text, nullable=True)

    received_by             = Column(Integer, ForeignKey("employees.id"), nullable=False)
    checked_by              = Column(Integer, ForeignKey("employees.id"), nullable=True)
    posted_at               = Column(DateTime(timezone=True), nullable=True)

    created_at              = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at              = Column(DateTime(timezone=True), onupdate=func.now())

    supplier        = relationship("Supplier",       foreign_keys=[supplier_id])
    purchase_order  = relationship("PurchaseOrder",  back_populates="grns")
    receiver        = relationship("Employee",       foreign_keys=[received_by])
    checker         = relationship("Employee",       foreign_keys=[checked_by])
    items           = relationship("GoodsReceivedItem", back_populates="grn",
                                   cascade="all, delete-orphan")
    invoice_matches = relationship("SupplierInvoiceMatch", back_populates="grn")


class GoodsReceivedItem(Base):
    __tablename__ = "goods_received_items"
    __table_args__ = (
        CheckConstraint("received_qty_purchase >= 0",  name="ck_gri_received_qty_non_negative"),
        CheckConstraint("damaged_qty_base >= 0",        name="ck_gri_damaged_qty_non_negative"),
        CheckConstraint("rejected_qty_base >= 0",       name="ck_gri_rejected_qty_non_negative"),
        CheckConstraint("units_per_purchase > 0",       name="ck_gri_units_per_purchase_positive"),
        CheckConstraint("cost_per_base_unit >= 0",      name="ck_gri_cost_non_negative"),
    )

    id                      = Column(Integer, primary_key=True, index=True)
    grn_id                  = Column(Integer, ForeignKey("goods_received_notes.id"), nullable=False, index=True)
    product_id              = Column(Integer, ForeignKey("products.id"),              nullable=False)
    purchase_order_item_id  = Column(Integer, ForeignKey("purchase_order_items.id"),  nullable=True)

    # What arrived at the door
    received_qty_purchase   = Column(Numeric(10, 3), nullable=False)    # e.g. 6 cartons
    purchase_unit_type      = Column(Enum(PurchaseUnitType), nullable=False, default=PurchaseUnitType.UNIT)
    units_per_purchase      = Column(Integer, nullable=False, default=1)
    received_qty_base       = Column(Integer, nullable=False)            # e.g. 144 bottles

    # Quality breakdown
    damaged_qty_base        = Column(Integer, nullable=False, default=0)
    rejected_qty_base       = Column(Integer, nullable=False, default=0)
    # accepted = received_base - damaged - rejected (computed)

    cost_per_base_unit      = Column(Numeric(12, 2), nullable=False, default=0)
    line_total              = Column(Numeric(12, 2), nullable=False, default=0)

    batch_number            = Column(String(100), nullable=True)
    expiry_date             = Column(Date,        nullable=True)
    notes                   = Column(Text,        nullable=True)

    grn     = relationship("GoodsReceivedNote",  back_populates="items")
    product = relationship("Product",            foreign_keys=[product_id])
    po_item = relationship("PurchaseOrderItem",  back_populates="grn_items")

    @property
    def accepted_qty_base(self) -> int:
        return max(0, self.received_qty_base - self.damaged_qty_base - self.rejected_qty_base)


# ── SupplierInvoiceMatch ──────────────────────────────────────────────────────

class SupplierInvoiceMatch(Base):
    __tablename__ = "supplier_invoice_matches"

    id                  = Column(Integer, primary_key=True, index=True)
    store_id            = Column(Integer, ForeignKey("stores.id"),          nullable=False, index=True)
    supplier_id         = Column(Integer, ForeignKey("suppliers.id"),        nullable=False, index=True)
    purchase_order_id   = Column(Integer, ForeignKey("purchase_orders.id"), nullable=True)
    grn_id              = Column(Integer, ForeignKey("goods_received_notes.id"), nullable=True)

    invoice_number      = Column(String(100), nullable=False)
    invoice_date        = Column(Date, nullable=True)
    invoice_total       = Column(Numeric(12, 2), nullable=False, default=0)

    matched_status      = Column(Enum(InvoiceMatchStatus), nullable=False,
                                  default=InvoiceMatchStatus.UNMATCHED, index=True)
    discrepancy_notes   = Column(Text, nullable=True)

    # Snapshot of computed variances (stored as JSON text for portability)
    variance_json       = Column(Text, nullable=True)

    created_by          = Column(Integer, ForeignKey("employees.id"), nullable=False)
    resolved_by         = Column(Integer, ForeignKey("employees.id"), nullable=True)
    resolved_at         = Column(DateTime(timezone=True), nullable=True)

    created_at          = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at          = Column(DateTime(timezone=True), onupdate=func.now())

    supplier        = relationship("Supplier",         foreign_keys=[supplier_id])
    purchase_order  = relationship("PurchaseOrder",    foreign_keys=[purchase_order_id])
    grn             = relationship("GoodsReceivedNote",back_populates="invoice_matches")
    creator         = relationship("Employee",         foreign_keys=[created_by])
    resolver        = relationship("Employee",         foreign_keys=[resolved_by])
