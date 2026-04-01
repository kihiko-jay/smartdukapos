from sqlalchemy import Column, Integer, String, Numeric, Boolean, DateTime, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        # FIX: phone uniqueness is per-store, not globally
        # Two different shops can have a customer with the same phone number
        UniqueConstraint("store_id", "phone", name="uq_customer_store_phone"),
    )

    id              = Column(Integer, primary_key=True, index=True)
    # FIX: add store_id — customers belong to a specific shop
    store_id        = Column(Integer, ForeignKey("stores.id"), nullable=True, index=True)
    name            = Column(String(150), nullable=False)
    phone           = Column(String(20), nullable=True, index=True)   # unique per store, not globally
    email           = Column(String(200), nullable=True)
    loyalty_points  = Column(Integer, default=0)
    # FIX: Float → Numeric(12,2) for KES precision
    credit_limit    = Column(Numeric(12, 2), default=0)
    credit_balance  = Column(Numeric(12, 2), default=0)
    notes           = Column(Text, nullable=True)
    is_active       = Column(Boolean, default=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())

    transactions = relationship("Transaction", back_populates="customer")
