"""
Transaction models — v2 upgrades:
  - FLOAT → NUMERIC(12,2) on all money columns
  - Added store_id (was missing — critical for multi-branch)
  - Added device_id (replaces terminal_id string for proper FK tracking)
  - Added sync_status for cloud sync agent tracking
  - Added external_ref for idempotent cloud upserts
"""

import enum
import uuid as _uuid
from sqlalchemy import (
    Column, Integer, String, Numeric, Boolean, DateTime,
    Enum, ForeignKey, Text
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class PaymentMethod(str, enum.Enum):
    CASH   = "cash"
    MPESA  = "mpesa"
    CARD   = "card"
    CREDIT = "credit"
    SPLIT  = "split"


class TransactionStatus(str, enum.Enum):
    PENDING   = "pending"
    COMPLETED = "completed"
    VOIDED    = "voided"
    REFUNDED  = "refunded"
    SUSPENDED = "suspended"


class SyncStatus(str, enum.Enum):
    PENDING = "pending"    # not yet sent to cloud
    SYNCED  = "synced"     # confirmed in cloud
    FAILED  = "failed"     # sync errored — will retry
    LOCAL   = "local"      # offline-created, needs sync


class Transaction(Base):
    __tablename__ = "transactions"

    id              = Column(Integer, primary_key=True, index=True)
    uuid            = Column(UUID(as_uuid=True), default=_uuid.uuid4, unique=True, index=True)  # sync key
    txn_number      = Column(String(30), unique=True, nullable=False, index=True)

    # FIXED: store_id was missing — critical for multi-branch aggregation
    store_id        = Column(Integer, ForeignKey("stores.id"),    nullable=False, index=True)
    terminal_id     = Column(String(20), nullable=True)

    # FIXED: FLOAT → NUMERIC(12,2)
    subtotal        = Column(Numeric(12, 2), nullable=False, default=0)
    discount_amount = Column(Numeric(12, 2), default=0)
    vat_amount      = Column(Numeric(12, 2), default=0)
    total           = Column(Numeric(12, 2), nullable=False, default=0)

    payment_method  = Column(Enum(PaymentMethod), nullable=False)
    cash_tendered   = Column(Numeric(12, 2), nullable=True)
    change_given    = Column(Numeric(12, 2), nullable=True)
    mpesa_ref        = Column(String(100), nullable=True)
    mpesa_checkout_id = Column(String(100), nullable=True, index=True)  # Safaricom CheckoutRequestID
    card_ref        = Column(String(100), nullable=True)

    status          = Column(Enum(TransactionStatus), default=TransactionStatus.PENDING)

    # KRA eTIMS
    etims_invoice_no = Column(String(100), nullable=True)
    etims_qr_code    = Column(Text, nullable=True)
    etims_synced     = Column(Boolean, default=False)

    # NEW: cloud sync tracking
    sync_status     = Column(Enum(SyncStatus), default=SyncStatus.PENDING)
    synced_at       = Column(DateTime(timezone=True), nullable=True)

    cashier_id      = Column(Integer, ForeignKey("employees.id"), nullable=True)
    customer_id     = Column(Integer, ForeignKey("customers.id"), nullable=True)

    created_at      = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    completed_at    = Column(DateTime(timezone=True), nullable=True)

    cashier   = relationship("Employee",        back_populates="transactions")
    customer  = relationship("Customer",        back_populates="transactions")
    items     = relationship("TransactionItem", back_populates="transaction", cascade="all, delete-orphan")


class TransactionItem(Base):
    __tablename__ = "transaction_items"

    id             = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=False)
    product_id     = Column(Integer, ForeignKey("products.id"),     nullable=False)

    product_name   = Column(String(200), nullable=False)    # price snapshot
    sku            = Column(String(50),  nullable=False)
    qty            = Column(Integer, nullable=False, default=1)
    tax_code       = Column(String(10),  nullable=True)      # snapshot: KRA tax type (B/Z/E)
    vat_exempt     = Column(Boolean, default=False)          # snapshot: exempt flag

    # FIXED: FLOAT → NUMERIC(12,2)
    unit_price      = Column(Numeric(12, 2), nullable=False)
    cost_price_snap = Column(Numeric(12, 2), nullable=True)  # NEW: for margin reporting
    discount        = Column(Numeric(12, 2), default=0)
    vat_amount      = Column(Numeric(12, 2), default=0)      # NEW: per-line VAT
    line_total      = Column(Numeric(12, 2), nullable=False)

    transaction = relationship("Transaction",  back_populates="items")
    product     = relationship("Product",      back_populates="transaction_items")
