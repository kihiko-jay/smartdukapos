"""
NEW: Audit trail router.
Provides read-only access to the audit log for managers and admins.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date

from app.core.deps import get_db, require_manager, require_premium
from app.models.audit import AuditTrail, SyncLog
from app.models.employee import Employee

router = APIRouter(prefix="/audit", tags=["Audit"])


@router.get("/trail", dependencies=[Depends(require_premium)])
def get_audit_trail(
    entity:     Optional[str] = None,
    entity_id:  Optional[str] = None,
    action:     Optional[str] = None,
    actor_id:   Optional[int] = None,
    date_from:  Optional[date]= None,
    date_to:    Optional[date]= None,
    skip:  int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current: Employee = Depends(require_manager),
):
    q = db.query(AuditTrail).order_by(AuditTrail.created_at.desc())
    # Fix: scope to caller's store — managers must not read another store's trail
    q = q.filter(AuditTrail.store_id == current.store_id)
    if entity:    q = q.filter(AuditTrail.entity    == entity)
    if entity_id: q = q.filter(AuditTrail.entity_id == entity_id)
    if action:    q = q.filter(AuditTrail.action    == action)
    if actor_id:  q = q.filter(AuditTrail.actor_id  == actor_id)
    if date_from:
        from sqlalchemy import cast, Date
        q = q.filter(cast(AuditTrail.created_at, Date) >= date_from)
    if date_to:
        from sqlalchemy import cast, Date
        q = q.filter(cast(AuditTrail.created_at, Date) <= date_to)
    rows = q.offset(skip).limit(limit).all()
    return {
        "count": len(rows),
        "entries": [
            {
                "id":         r.id,
                "actor":      r.actor_name,
                "action":     r.action,
                "entity":     r.entity,
                "entity_id":  r.entity_id,
                "before":     r.before_val,
                "after":      r.after_val,
                "notes":      r.notes,
                "created_at": str(r.created_at),
            }
            for r in rows
        ],
    }


@router.get("/sync-log", dependencies=[Depends(require_premium)])
def get_sync_log(
    entity:    Optional[str] = None,
    status:    Optional[str] = None,
    skip:  int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    _=Depends(require_manager),
):
    q = db.query(SyncLog).order_by(SyncLog.synced_at.desc())
    if entity: q = q.filter(SyncLog.entity == entity)
    if status: q = q.filter(SyncLog.status == status)
    rows = q.offset(skip).limit(limit).all()
    return {
        "count": len(rows),
        "entries": [
            {
                "id":          r.id,
                "entity":      r.entity,
                "direction":   r.direction,
                "status":      r.status,
                "records_in":  r.records_in,
                "records_out": r.records_out,
                "checkpoint":  r.checkpoint,
                "error_msg":   r.error_msg,
                "duration_ms": r.duration_ms,
                "synced_at":   str(r.synced_at),
            }
            for r in rows
        ],
    }
