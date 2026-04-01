"""
NEW: Audit trail + sync log tables.

audit_trail  — append-only compliance log for all state changes (KRA requirement)
sync_log     — every sync agent operation is recorded here for observability
"""

from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, BigInteger
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB
from sqlalchemy import event as _sa_event

# Use JSONB on PostgreSQL; fall back to plain JSON on SQLite (test suite).
# This is a transparent type alias — no application logic changes needed.
import sqlalchemy as _sa
JSONB = _sa.JSON().with_variant(_PG_JSONB(), 'postgresql')
from sqlalchemy.sql import func
from app.database import Base


class AuditTrail(Base):
    """
    Immutable append-only log of every meaningful state change.
    Never UPDATE or DELETE from this table.
    Used for KRA compliance and dispute resolution.
    """
    __tablename__ = "audit_trail"

    id          = Column(BigInteger, primary_key=True, index=True)
    store_id    = Column(Integer, ForeignKey("stores.id"), nullable=True, index=True)
    actor_id    = Column(Integer, nullable=True)                          # employee who triggered change
    actor_name  = Column(String(150), nullable=True)                      # snapshot — survives employee delete
    action      = Column(String(50),  nullable=False, index=True)         # create|update|void|refund|stock_adj|login
    entity      = Column(String(50),  nullable=False, index=True)         # transaction|product|employee|customer
    entity_id   = Column(String(100), nullable=False)                     # PK or txn_number
    before_val  = Column(JSONB, nullable=True)                            # previous state snapshot
    after_val   = Column(JSONB, nullable=True)                            # new state snapshot
    ip_address  = Column(String(45),  nullable=True)
    user_agent  = Column(String(300), nullable=True)
    notes       = Column(Text, nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    # NO update_at — this table is append-only


class SyncLog(Base):
    """
    NEW: Records every sync agent operation.
    Provides observability into the sync pipeline.
    """
    __tablename__ = "sync_log"

    id          = Column(BigInteger, primary_key=True, index=True)
    entity      = Column(String(50),  nullable=False, index=True)   # products|transactions|customers|employees
    entity_id   = Column(String(100), nullable=True)
    direction   = Column(String(20),  nullable=False)                # local_to_cloud | cloud_to_local
    status      = Column(String(20),  nullable=False, index=True)    # success|conflict|error|retry|skipped
    records_in  = Column(Integer, default=0)                         # how many records processed this batch
    records_out = Column(Integer, default=0)                         # how many successfully written
    conflict    = Column(JSONB, nullable=True)                       # both versions stored on conflict
    error_msg   = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)                     # how long the sync took
    checkpoint  = Column(String(50), nullable=True)                  # last updated_at processed
    synced_at   = Column(DateTime(timezone=True), server_default=func.now(), index=True)
