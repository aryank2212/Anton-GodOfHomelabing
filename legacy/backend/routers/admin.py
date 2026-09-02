from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone
from typing import Optional

from ..database import get_db
from .. import models, schemas, auth
from ..services.audit_service import record_audit, get_audit_logs
from ..services.backup_service import create_backup, restore_backup, list_backups
from ..services.queue_service import queue

router = APIRouter(prefix="/api/admin", tags=["Admin"])


def _require_admin(request: Request, db: Session):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin"])
    if auth.user_must_change_password(user):
        raise HTTPException(403, "Password change required before this action")
    return user


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@router.get("/dashboard", response_model=schemas.DashboardResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
):
    _require_admin(request, db)
    now = _now()

    active_sessions_q = db.query(models.Session).filter(
        models.Session.expires_at > now
    )
    active_sessions = active_sessions_q.count()
    online_ids = {
        row[0]
        for row in active_sessions_q.with_entities(models.Session.user_id).distinct().all()
    }
    total_users = db.query(models.User).count()
    total_entries = db.query(models.Entry).count()
    verified_users = db.query(models.User).filter(
        models.User.email.isnot(None),
        models.User.email_verified == 1,
    ).count()

    entry_counts = dict(
        db.query(models.Entry.user_id, func.count(models.Entry.id))
        .group_by(models.Entry.user_id).all()
    )
    session_counts = dict(
        db.query(models.Session.user_id, func.count(models.Session.id))
        .group_by(models.Session.user_id).all()
    )
    last_seen = {}
    last_ip = {}
    for s in db.query(models.Session).order_by(
        models.Session.created_at.desc()
    ).all():
        if s.user_id not in last_seen:
            last_seen[s.user_id] = s.created_at
            last_ip[s.user_id] = s.ip_address

    users = []
    for u in db.query(models.User).order_by(models.User.created_at.desc()).all():
        users.append(schemas.AdminUserRow(
            id=u.id,
            username=u.username,
            email=u.email,
            role=u.role,
            is_active=bool(u.is_active),
            display_name=u.display_name,
            email_verified=bool(u.email_verified),
            must_change_password=bool(u.must_change_password),
            created_at=u.created_at,
            last_login_at=u.last_login_at,
            online=u.id in online_ids,
            last_ip=last_ip.get(u.id),
            last_seen=last_seen.get(u.id),
            entry_count=entry_counts.get(u.id, 0),
            session_count=session_counts.get(u.id, 0),
        ))

    sessions = []
    usernames = {u.id: u.username for u in db.query(models.User).all()}
    for s in db.query(models.Session).order_by(
        models.Session.created_at.desc()
    ).limit(25).all():
        sessions.append(schemas.AdminSessionRow(
            id=s.id,
            user_id=s.user_id,
            username=usernames.get(s.user_id),
            ip_address=s.ip_address,
            user_agent=s.user_agent,
            active=_aware(s.expires_at) > now,
            created_at=s.created_at,
            last_accessed_at=s.last_accessed_at,
            expires_at=s.expires_at,
        ))

    recent_audit = get_audit_logs(db, limit=10)

    return schemas.DashboardResponse(
        total_users=total_users,
        online_users=len(online_ids),
        active_sessions=active_sessions,
        total_entries=total_entries,
        verified_users=verified_users,
        users=users,
        sessions=sessions,
        recent_audit=recent_audit,
    )


@router.get("/entries", response_model=list[schemas.AdminEntryRow])
def list_all_entries(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = Query(500, ge=1, le=2000),
):
    _require_admin(request, db)
    rows = []
    entries = db.query(models.Entry).order_by(
        models.Entry.created_at.desc()
    ).limit(limit).all()
    for e in entries:
        username = None
        if e.user_id:
            user = db.query(models.User).filter(models.User.id == e.user_id).first()
            username = user.username if user else None
        rows.append(schemas.AdminEntryRow(
            id=e.id,
            user_id=e.user_id,
            username=username,
            title=e.title,
            content=e.content,
            entry_type=e.entry_type,
            visibility=e.visibility,
            source=e.source,
            mood=e.mood,
            created_at=e.created_at,
            updated_at=e.updated_at,
        ))
    return rows


@router.get("/users/{user_id}/entries", response_model=list[schemas.EntryResponse])
def list_user_entries(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    limit: int = Query(200, ge=1, le=1000),
):
    _require_admin(request, db)
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    return db.query(models.Entry).filter(
        models.Entry.user_id == user_id
    ).order_by(models.Entry.created_at.desc()).limit(limit).all()


@router.get("/users/{user_id}/sessions", response_model=list[schemas.AdminSessionRow])
def list_user_sessions(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_admin(request, db)
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    now = _now()
    rows = []
    for s in db.query(models.Session).filter(
        models.Session.user_id == user_id
    ).order_by(models.Session.created_at.desc()).limit(50).all():
        rows.append(schemas.AdminSessionRow(
            id=s.id,
            user_id=s.user_id,
            ip_address=s.ip_address,
            user_agent=s.user_agent,
            active=_aware(s.expires_at) > now,
            created_at=s.created_at,
            last_accessed_at=s.last_accessed_at,
            expires_at=s.expires_at,
        ))
    return rows


@router.get("/users", response_model=list[schemas.UserResponse])
def list_users(
    request: Request,
    db: Session = Depends(get_db),
):
    _require_admin(request, db)
    return db.query(models.User).order_by(models.User.username).all()


@router.get("/users/{user_id}", response_model=schemas.UserResponse)
def get_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_admin(request, db)
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    return user


@router.put("/users/{user_id}", response_model=schemas.UserResponse)
def update_user(
    user_id: int,
    data: schemas.UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    admin = _require_admin(request, db)
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")

    if data.email is not None:
        user.email = data.email
    if data.display_name is not None:
        user.display_name = data.display_name
    if data.role is not None:
        user.role = data.role
    if data.is_active is not None:
        user.is_active = 1 if data.is_active else 0

    db.commit()
    db.refresh(user)

    record_audit(
        db, "update", "user", admin.id, admin.username,
        resource_type="user", resource_id=user_id,
        details={"updated_fields": data.model_dump(exclude_none=True)},
    )
    return user


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    admin = _require_admin(request, db)
    if user_id == admin.id:
        raise HTTPException(400, "Cannot delete yourself")

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")

    record_audit(
        db, "delete", "user", admin.id, admin.username,
        resource_type="user", resource_id=user_id,
    )
    db.delete(user)
    db.commit()
    return {"status": "deleted"}


@router.get("/audit", response_model=list[schemas.AuditLogResponse])
def list_audit(
    request: Request,
    db: Session = Depends(get_db),
    action: Optional[str] = Query(None),
    actor_type: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    limit: int = Query(100, le=1000),
    offset: int = Query(0),
):
    _require_admin(request, db)
    return get_audit_logs(
        db, action=action, actor_type=actor_type,
        resource_type=resource_type, limit=limit, offset=offset,
    )


@router.get("/audit/stats")
def audit_stats(
    request: Request,
    db: Session = Depends(get_db),
):
    _require_admin(request, db)
    total = db.query(models.AuditLog).count()

    action_counts = {}
    for row in db.query(models.AuditLog.action, models.AuditLog.action).distinct().all():
        count = db.query(models.AuditLog).filter(
            models.AuditLog.action == row[0]
        ).count()
        action_counts[row[0]] = count

    return {
        "total": total,
        "by_action": action_counts,
    }


@router.post("/backup")
def trigger_backup(
    request: Request,
    db: Session = Depends(get_db),
    backup_type: str = Query("full"),
):
    admin = _require_admin(request, db)
    result = create_backup(db, backup_type)
    record_audit(
        db, "backup", "user", admin.id, admin.username,
        resource_type="system",
        details=result,
    )
    return result


@router.get("/backups", response_model=list[schemas.BackupResponse])
def get_backups(
    request: Request,
    db: Session = Depends(get_db),
):
    _require_admin(request, db)
    return list_backups(db)


@router.post("/backups/restore")
def restore(
    request: Request,
    db: Session = Depends(get_db),
    filename: str = Query(...),
    selective: Optional[str] = Query(None),
):
    admin = _require_admin(request, db)
    selective_list = selective.split(",") if selective else None
    result = restore_backup(db, filename, selective_list)
    record_audit(
        db, "restore", "user", admin.id, admin.username,
        resource_type="system",
        details={"filename": filename, **result},
    )
    return result


@router.get("/queue")
def get_queue_status(
    request: Request,
    db: Session = Depends(get_db),
):
    _require_admin(request, db)
    return queue.get_status()


@router.post("/queue/jobs")
def create_job(
    request: Request,
    db: Session = Depends(get_db),
    job_type: str = Query(...),
    payload: Optional[str] = Query(None),
    priority: int = Query(0),
):
    _require_admin(request, db)
    import json
    payload_dict = json.loads(payload) if payload else {}
    job_id = queue.enqueue(job_type, payload_dict, priority)
    return {"job_id": job_id, "job_type": job_type}


@router.get("/system/version")
def system_version():
    from ..config import config
    return {
        "name": config.app.get("name", "LEGACY"),
        "version": config.app.get("version", "3.0.0"),
        "auth_enabled": auth.AUTH_ENABLED,
    }
