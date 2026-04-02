"""
Product models — v2 upgrades:
  - FLOAT → NUMERIC(12,2) for all money fields (prevents KES rounding errors)
  - Added supplier_id FK for purchase order linkage
  - Added tax_code for granular KRA eTIMS classification
  - stock_quantity now synced via stock_movements ledger
  - Added uuid column for distributed sync identity
"""

import uuid as _uuid
from sqlalchemy import Column, Integer, String, Numeric, Boolean, DateTime, Text, ForeignKey, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy import func
from app.database import Base


class Category(Base):
    __tablename__ = "categories"

    id          = Column(Integer, primary_key=True, index=True)
    store_id    = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    name        = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    parent_id   = Column(Integer, ForeignKey("categories.id"), nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    products = relationship("Product", back_populates="category")
    children = relationship("Category", backref="parent", remote_side="Category.id")


class Supplier(Base):
    """NEW: Vendor master for purchase orders."""
    __tablename__ = "suppliers"

    id           = Column(Integer, primary_key=True, index=True)
    store_id     = Column(Integer, ForeignKey("stores.id"), nullable=False)
    name         = Column(String(200), nullable=False)
    contact_name = Column(String(150), nullable=True)
    phone        = Column(String(20),  nullable=True)
    email        = Column(String(200), nullable=True)
    address      = Column(Text,        nullable=True)
    kra_pin      = Column(String(50),  nullable=True)
    is_active    = Column(Boolean, default=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())

    products = relationship("Product", back_populates="supplier")
    purchase_orders = relationship("PurchaseOrder", foreign_keys="PurchaseOrder.supplier_id")


class Product(Base):
    __tablename__ = "products"
    # Fix: uniqueness is per-store (migration 0007 created these DB constraints).
    # Removing global unique=True from sku/barcode — ORM now matches the DB.
    __table_args__ = (
        UniqueConstraint("store_id", "sku",     name="uq_product_sku_per_store"),
        UniqueConstraint("store_id", "barcode", name="uq_product_barcode_per_store"),
    )

    id              = Column(Integer, primary_key=True, index=True)
    store_id        = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    uuid            = Column(UUID(as_uuid=True), default=_uuid.uuid4, unique=True, index=True)
    sku             = Column(String(50),  nullable=False, index=True)
    barcode         = Column(String(100), nullable=True,  index=True)
    itemcode        = Column(Integer, nullable=True, index=True)  # NEW: numeric code for fast POS lookup
    name            = Column(String(200), nullable=False)
    description     = Column(Text, nullable=True)
    category_id     = Column(Integer, ForeignKey("categories.id"), nullable=True)
    supplier_id     = Column(Integer, ForeignKey("suppliers.id"),  nullable=True)

    # FIXED: FLOAT → NUMERIC(12,2) — prevents KES rounding errors
    selling_price   = Column(Numeric(12, 2), nullable=False)
    cost_price      = Column(Numeric(12, 2), nullable=True)

    vat_exempt      = Column(Boolean,     default=False)
    tax_code        = Column(String(10),  default="B")   # A=exempt, B=16% standard

    stock_quantity  = Column(Integer, default=0)
    reorder_level   = Column(Integer, default=10)
    unit            = Column(String(30), default="piece")

    is_active       = Column(Boolean,      default=True)
    image_url       = Column(String(500),  nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())

    category          = relationship("Category",      back_populates="products")
    store             = relationship("Store")
    supplier          = relationship("Supplier",      back_populates="products")
    transaction_items = relationship("TransactionItem", back_populates="product")
    stock_movements   = relationship("StockMovement",   back_populates="product")
    packaging         = relationship("ProductPackaging", back_populates="product",
                                     cascade="all, delete-orphan")

    @property
    def is_low_stock(self) -> bool:
        return self.stock_quantity <= self.reorder_level

    @property
    def stock_value(self) -> float:
        cost = float(self.cost_price or self.selling_price)
        return round(cost * self.stock_quantity, 2)


class StockMovement(Base):
    """
    NEW: Explicit stock ledger.
    Every qty change (sale, purchase, adjustment, write-off) is recorded here.
    product.stock_quantity is kept as a fast-read cache; this table is authoritative.
    """
    __tablename__ = "stock_movements"

    id            = Column(Integer, primary_key=True, index=True)
    product_id    = Column(Integer, ForeignKey("products.id"),  nullable=False, index=True)
    store_id      = Column(Integer, ForeignKey("stores.id"),    nullable=False)
    movement_type = Column(String(20), nullable=False)    # sale|purchase|adjustment|write_off|void_restore|sync
    qty_delta     = Column(Integer, nullable=False)        # negative=out, positive=in
    qty_before    = Column(Integer, nullable=False)
    qty_after     = Column(Integer, nullable=False)
    ref_id        = Column(String(50),  nullable=True)     # txn_number or PO id
    notes         = Column(Text,        nullable=True)
    performed_by  = Column(Integer, ForeignKey("employees.id"), nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    product  = relationship("Product",  back_populates="stock_movements")
    employee = relationship("Employee", foreign_keys=[performed_by])
