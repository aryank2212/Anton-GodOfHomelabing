import json
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from .. import models
from ..logging_config import get_logger

logger = get_logger("legacy.audit")


def record_audit(
    db: Session,
    action: str,
    actor_type: str,
    actor_id: Optional[int] = None,
    actor_name: Optional[str] = None,
    resource_type: str = "",
    resource_id: Optional[int] = None,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    duration_ms: Optional[int] = None,
    status: str = "success",
) -> models.AuditLog:
    entry = models.AuditLog(
        action=action,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_name=actor_name,
        resource_type=resource_type,
        resource_id=resource_id,
        details=json.dumps(details) if details else None,
        ip_address=ip_address,
        user_agent=user_agent,
        duration_ms=duration_ms,
        status=status,
    )
    db.add(entry)
    db.commit()
    return entry


def get_audit_logs(
    db: Session,
    action: Optional[str] = None,
    actor_type: Optional[str] = None,
    actor_id: Optional[int] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[models.AuditLog]:
    query = db.query(models.AuditLog)

    if action:
        query = query.filter(models.AuditLog.action == action)
    if actor_type:
        query = query.filter(models.AuditLog.actor_type == actor_type)
    if actor_id is not None:
        query = query.filter(models.AuditLog.actor_id == actor_id)
    if resource_type:
        query = query.filter(models.AuditLog.resource_type == resource_type)
    if resource_id is not None:
        query = query.filter(models.AuditLog.resource_id == resource_id)

    return (
        query
        .order_by(models.AuditLog.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
