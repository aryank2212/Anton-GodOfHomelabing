import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from .. import models


EVENT_SOURCES = ["watcher", "hermes", "sentinel", "phoenix", "system", "memory"]


def publish_event(
    db: Session,
    source: str,
    event_type: str,
    severity: str = "info",
    title: str | None = None,
    description: str | None = None,
    metadata_json: str | None = None,
) -> models.Event:
    event = models.Event(
        source=source,
        event_type=event_type,
        severity=severity,
        title=title,
        description=description,
        metadata_json=metadata_json,
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    if "memory" in db.__class__.__module__:
        try:
            from .memory_service import store_observation
            if source != "memory":
                display = title or event_type
                store_observation(
                    db,
                    f"[{source.upper()}] {display}: {description or ''}",
                    title=f"System: {display}",
                )
        except Exception:
            pass

    return event


def get_events(
    db: Session,
    source: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[models.Event]:
    query = db.query(models.Event)

    if source:
        query = query.filter(models.Event.source == source)
    if event_type:
        query = query.filter(models.Event.event_type == event_type)
    if severity:
        query = query.filter(models.Event.severity == severity)

    return query.order_by(models.Event.created_at.desc()).offset(offset).limit(limit).all()


def get_event_stats(db: Session) -> dict:
    total = db.query(models.Event).count()

    by_source = {}
    for source in EVENT_SOURCES:
        if source == "memory":
            continue
        count = db.query(models.Event).filter(models.Event.source == source).count()
        if count:
            by_source[source] = count

    last_24h = db.query(models.Event).filter(
        models.Event.created_at >= datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
    ).count()

    return {
        "total": total,
        "by_source": by_source,
        "last_24h": last_24h,
    }
