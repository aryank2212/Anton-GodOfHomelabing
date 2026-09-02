from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas, auth
from ..services.event_service import publish_event, get_events, get_event_stats
from ..services.audit_service import record_audit

router = APIRouter(prefix="/api/events", tags=["Events"])


@router.post("")
def api_publish_event(
    data: schemas.EventCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent"])

    event = publish_event(
        db=db,
        source=data.source,
        event_type=data.event_type,
        severity=data.severity,
        title=data.title,
        description=data.description,
        metadata_json=data.metadata_json,
    )

    record_audit(
        db, "create", "user" if user else "agent",
        actor_id=user.id if user else None,
        resource_type="event", resource_id=event.id,
    )

    return {
        "id": event.id,
        "source": event.source,
        "event_type": event.event_type,
        "title": event.title,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


@router.get("")
def api_get_events(
    request: Request,
    source: str = Query(None),
    event_type: str = Query(None),
    severity: str = Query(None),
    limit: int = Query(50),
    offset: int = Query(0),
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent", "read-only"])

    events = get_events(db, source=source, event_type=event_type, severity=severity, limit=limit, offset=offset)
    return [
        {
            "id": e.id,
            "source": e.source,
            "event_type": e.event_type,
            "severity": e.severity,
            "title": e.title,
            "description": e.description,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]


@router.get("/stats")
def api_event_stats(
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent", "read-only"])
    return get_event_stats(db)


@router.get("/{event_id}")
def api_get_event(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent", "read-only"])

    event = db.query(models.Event).filter(models.Event.id == event_id).first()
    if not event:
        raise HTTPException(404, "Event not found")

    return {
        "id": event.id,
        "source": event.source,
        "event_type": event.event_type,
        "severity": event.severity,
        "title": event.title,
        "description": event.description,
        "metadata_json": event.metadata_json,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


@router.delete("/{event_id}")
def api_delete_event(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin"])

    event = db.query(models.Event).filter(models.Event.id == event_id).first()
    if not event:
        raise HTTPException(404, "Event not found")

    record_audit(
        db, "delete", "user", user.id if user else None,
        resource_type="event", resource_id=event_id,
    )

    db.delete(event)
    db.commit()
    return {"status": "deleted", "id": event_id}
