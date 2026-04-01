import enum
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum, Float, Numeric, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Plan(str, enum.Enum):
    FREE    = "free"      # POS only — forever free
    STARTER = "starter"   # KES 1,500/mo — 1 store, all back-office
    GROWTH  = "growth"    # KES 3,500/mo — up to 3 stores
    PRO     = "pro"       # KES 7,500/mo — unlimited stores + API


class SubStatus(str, enum.Enum):
    ACTIVE   = "active"
    TRIALING = "trialing"   # 14-day trial on signup
    EXPIRED  = "expired"
    CANCELLED = "cancelled"


class Store(Base):
    """
    A Store is the top-level tenant. Every employee and transaction
    belongs to a store. The subscription is per store.
    """
    __tablename__ = "stores"

    id            = Column(Integer, primary_key=True, index=True)
    name          = Column(String(200), nullable=False)
    location      = Column(String(300), nullable=True)
    kra_pin       = Column(String(50),  nullable=True)
    phone         = Column(String(20),  nullable=True)
    email         = Column(String(200), nullable=True)

    # Subscription
    plan          = Column(Enum(Plan),      default=Plan.FREE,     nullable=False)
    sub_status    = Column(Enum(SubStatus), default=SubStatus.TRIALING, nullable=False)
    trial_ends_at = Column(DateTime(timezone=True), nullable=True)
    sub_ends_at   = Column(DateTime(timezone=True), nullable=True)

    # M-PESA billing ref (for auto-renewal)
    mpesa_phone   = Column(String(20),  nullable=True)
    billing_ref   = Column(String(100), nullable=True)

    is_active     = Column(Boolean, default=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), onupdate=func.now())

    employees     = relationship("Employee",    back_populates="store")
    payments      = relationship("SubPayment",  back_populates="store")

    @property
    def is_premium(self) -> bool:
        """True if this store has an active paid plan or valid trial."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        if self.sub_status == SubStatus.TRIALING:
            return self.trial_ends_at and self.trial_ends_at > now
        if self.sub_status == SubStatus.ACTIVE:
            return self.sub_ends_at is None or self.sub_ends_at > now
        return False

    @property
    def plan_label(self) -> str:
        labels = {
            Plan.FREE:    "Free",
            Plan.STARTER: "Starter — KES 1,500/mo",
            Plan.GROWTH:  "Growth — KES 3,500/mo",
            Plan.PRO:     "Pro — KES 7,500/mo",
        }
        return labels.get(self.plan, self.plan)


class SubPayment(Base):
    """Records every subscription payment (M-PESA)."""
    __tablename__ = "sub_payments"

    id          = Column(Integer, primary_key=True, index=True)
    store_id    = Column(Integer, ForeignKey("stores.id"), nullable=False)
    amount      = Column(Numeric(12, 2), nullable=False)  # Fixed: was Float — inconsistent with all other money fields
    plan        = Column(Enum(Plan), nullable=False)
    mpesa_ref   = Column(String(100), nullable=True)
    months      = Column(Integer, default=1)
    status      = Column(String(20), default="pending")  # pending | confirmed
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    store = relationship("Store", back_populates="payments")
