from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_
from .. import models
from ..auth import check_visibility
from ..utils.tags import parse_tags


def search_knowledge(
    db: Session,
    q: str = "",
    sources: list[str] | None = None,
    limit: int = 20,
    user: Optional[models.User] = None,
) -> list[dict]:
    results = []

    entry_q = db.query(models.Entry)
    if q:
        entry_q = entry_q.filter(
            or_(
                models.Entry.title.contains(q),
                models.Entry.content.contains(q),
                models.Entry.tags.contains(q),
            )
        )
    if sources:
        entry_q = entry_q.filter(models.Entry.source.in_(sources))

    if user and user.role != "admin":
        entry_q = entry_q.filter(
            or_(
                models.Entry.visibility == "public",
                models.Entry.user_id == user.id,
            )
        )
    elif not user:
        entry_q = entry_q.filter(models.Entry.visibility == "public")

    for e in entry_q.order_by(models.Entry.created_at.desc()).limit(limit).all():
        results.append({
            "type": "entry",
            "id": e.id,
            "title": e.title,
            "content": e.content[:500],
            "source": e.source,
            "entry_type": e.entry_type,
            "tags": parse_tags(e.tags),
            "created_at": e.created_at.isoformat() if e.created_at else None,
        })

    event_q = db.query(models.Event)
    if q:
        event_q = event_q.filter(
            or_(
                models.Event.title.contains(q),
                models.Event.description.contains(q),
                models.Event.event_type.contains(q),
            )
        )
    for ev in event_q.order_by(models.Event.created_at.desc()).limit(limit).all():
        results.append({
            "type": "event",
            "id": ev.id,
            "title": ev.title,
            "content": ev.description or "",
            "source": ev.source,
            "event_type": ev.event_type,
            "severity": ev.severity,
            "created_at": ev.created_at.isoformat() if ev.created_at else None,
        })

    results.sort(key=lambda x: x["created_at"], reverse=True)
    return results[:limit]


def get_context(
    db: Session,
    topics: list[str],
    max_entries: int = 5,
    max_events: int = 5,
    user: Optional[models.User] = None,
) -> str:
    context_parts = []

    try:
        from .embedding_service import is_available
    except ImportError:
        is_available = lambda: False

    for topic in topics:
        context_parts.append(f"## Context: {topic}")

        entry_q = db.query(models.Entry).filter(
            or_(
                models.Entry.title.contains(topic),
                models.Entry.content.contains(topic),
                models.Entry.tags.contains(topic),
            )
        )
        if user and user.role != "admin":
            entry_q = entry_q.filter(
                or_(
                    models.Entry.visibility == "public",
                    models.Entry.user_id == user.id,
                )
            )
        elif not user:
            entry_q = entry_q.filter(models.Entry.visibility == "public")

        for e in entry_q.order_by(models.Entry.created_at.desc()).limit(max_entries).all():
            context_parts.append(f"- [{e.entry_type}] {e.title or 'Untitled'}: {e.content[:400]}")

        events = db.query(models.Event).filter(
            or_(
                models.Event.title.contains(topic),
                models.Event.description.contains(topic),
            )
        ).order_by(models.Event.created_at.desc()).limit(max_events).all()

        for ev in events:
            context_parts.append(f"- [{ev.source.upper()} Event] {ev.title or ev.event_type}: {ev.description or ''}")

    return "\n\n".join(context_parts)


def generate_summary(db: Session, entry_ids: list[int]) -> str:
    entries = db.query(models.Entry).filter(models.Entry.id.in_(entry_ids)).all()
    if not entries:
        return "No entries found."

    parts = []
    for e in sorted(entries, key=lambda x: x.created_at):
        date_str = e.created_at.strftime("%Y-%m-%d") if e.created_at else "unknown"
        parts.append(f"[{date_str}] {e.title or 'Untitled'} ({e.entry_type}): {e.content[:500]}")

    return "\n\n".join(parts)
