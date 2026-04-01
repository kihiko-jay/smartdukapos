import enum
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Role(str, enum.Enum):
    CASHIER         = "cashier"
    SUPERVISOR      = "supervisor"
    MANAGER         = "manager"
    ADMIN           = "admin"
    PLATFORM_OWNER  = "platform_owner"   # You only — never visible to shops


class Employee(Base):
    __tablename__ = "employees"

    id           = Column(Integer, primary_key=True, index=True)
    # NULL store_id = platform owner account (not scoped to any shop)
    store_id     = Column(Integer, ForeignKey("stores.id"), nullable=True)
    full_name    = Column(String(150), nullable=False)
    email        = Column(String(200), unique=True, nullable=False, index=True)
    phone        = Column(String(20),  nullable=True)
    pin          = Column(String(200), nullable=True)   # bcrypt-hashed
    password     = Column(String(200), nullable=False)
    role         = Column(Enum(Role),  default=Role.CASHIER, nullable=False)
    is_active    = Column(Boolean, default=True)
    terminal_id  = Column(String(20),  nullable=True)

    clocked_in_at  = Column(DateTime(timezone=True), nullable=True)
    clocked_out_at = Column(DateTime(timezone=True), nullable=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    updated_at     = Column(DateTime(timezone=True), onupdate=func.now())

    store        = relationship("Store",       back_populates="employees")
    transactions = relationship("Transaction", back_populates="cashier")
