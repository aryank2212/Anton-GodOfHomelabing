import json
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_
from .. import models
from ..auth import check_visibility
from ..utils.tags import parse_tags
from .embedding_service import generate_embedding, parse_embedding, cosine_similarity
from .event_service import publish_event


MEMORY_TYPES = [
    "Journal", "Dream", "Idea", "Conversation", "Observation",
    "Server Event", "Training Log", "Deployment", "System Alert",
    "Meeting", "Book Note", "Research",
]


def create_memory(db: Session, data: dict) -> models.Entry:
    entry = models.Entry(
        title=data.get("title"),
        content=data["content"],
        entry_type=data.get("entry_type", "Journal"),
        tags=data.get("tags"),
        mood=data.get("mood"),
        visibility=data.get("visibility", "private"),
        source=data.get("source", "journal"),
        metadata_json=data.get("metadata_json"),
        user_id=data.get("user_id"),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    if data.get("source") != "journal" or len(data.get("content", "")) > 50:
        publish_event(
            db=db,
            source="memory",
            event_type="memory_created",
            severity="info",
            title=data.get("title") or "Memory created",
            description=data["content"][:200],
            metadata_json=json.dumps({"entry_id": entry.id, "entry_type": entry.entry_type}),
        )

    _process_entities(db, entry)
    return entry


def get_memory(db: Session, memory_id: int) -> models.Entry | None:
    return db.query(models.Entry).filter(models.Entry.id == memory_id).first()


def get_event_memory(db: Session, event_id: int) -> models.Event | None:
    return db.query(models.Event).filter(models.Event.id == event_id).first()


def search_memories(
    db: Session,
    q: str | None = None,
    source: str | None = None,
    entry_type: str | None = None,
    visibility: str | None = None,
    mood: str | None = None,
    tag: str | None = None,
    limit: int = 20,
    offset: int = 0,
    user: Optional[models.User] = None,
) -> list[dict]:
    results = []

    query = db.query(models.Entry)

    if q:
        query = query.filter(
            or_(
                models.Entry.title.contains(q),
                models.Entry.content.contains(q),
            )
        )
    if source:
        query = query.filter(models.Entry.source == source)
    if entry_type:
        query = query.filter(models.Entry.entry_type == entry_type)
    if visibility:
        query = query.filter(models.Entry.visibility == visibility)
    if mood:
        query = query.filter(models.Entry.mood == mood)
    if tag:
        query = query.filter(models.Entry.tags.contains(tag))

    if user and user.role != "admin":
        query = query.filter(
            or_(
                models.Entry.visibility == "public",
                models.Entry.user_id == user.id,
            )
        )
    elif not user:
        query = query.filter(models.Entry.visibility == "public")

    entries = query.order_by(models.Entry.created_at.desc()).offset(offset).limit(limit).all()

    for e in entries:
        results.append({
            "id": e.id,
            "type": "entry",
            "title": e.title,
            "content": e.content[:300],
            "source": e.source,
            "entry_type": e.entry_type,
            "visibility": e.visibility,
            "mood": e.mood,
            "tags": e.tags,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        })

    return results


def semantic_search(
    db: Session,
    query_text: str,
    limit: int = 10,
    user: Optional[models.User] = None,
) -> list[dict]:
    query_emb = generate_embedding(query_text)
    if not query_emb:
        return []

    query_vec = parse_embedding(query_emb)
    if not query_vec:
        return []

    entries = db.query(models.Entry).filter(
        models.Entry.embedding.isnot(None)
    ).all()

    scored = []
    for e in entries:
        if not check_visibility(e.visibility, user, e.user_id):
            continue
        ev = parse_embedding(e.embedding)
        if ev:
            sim = cosine_similarity(query_vec, ev)
            scored.append((sim, e))

    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for sim, e in scored[:limit]:
        results.append({
            "id": e.id,
            "type": "entry",
            "title": e.title,
            "content": e.content[:300],
            "source": e.source,
            "entry_type": e.entry_type,
            "visibility": e.visibility,
            "similarity": round(sim, 4),
            "created_at": e.created_at.isoformat() if e.created_at else None,
        })

    return results


def delete_memory(db: Session, memory_id: int) -> bool:
    entry = db.query(models.Entry).filter(models.Entry.id == memory_id).first()
    if not entry:
        return False
    db.delete(entry)
    db.commit()
    return True


def store_observation(
    db: Session,
    content: str,
    title: str | None = None,
    user_id: Optional[int] = None,
) -> models.Entry:
    return create_memory(db, {
        "title": title or f"Observation: {content[:60]}...",
        "content": content,
        "entry_type": "Observation",
        "source": "memory",
        "visibility": "private",
        "user_id": user_id,
    })


def get_memory_timeline(
    db: Session,
    since: datetime | None = None,
    source: str | None = None,
    entry_type: str | None = None,
    limit: int = 50,
    user: Optional[models.User] = None,
) -> list[dict]:
    items = []

    entry_q = db.query(models.Entry)
    if since:
        entry_q = entry_q.filter(models.Entry.created_at >= since)
    if source:
        entry_q = entry_q.filter(models.Entry.source == source)
    if entry_type:
        entry_q = entry_q.filter(models.Entry.entry_type == entry_type)

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
        items.append({
            "id": e.id,
            "timeline_type": "entry",
            "title": e.title,
            "content": e.content[:300],
            "source": e.source,
            "entry_type": e.entry_type,
            "created_at": e.created_at,
        })

    event_q = db.query(models.Event)
    if since:
        event_q = event_q.filter(models.Event.created_at >= since)
    if source and source in ("watcher", "hermes", "sentinel", "phoenix", "system"):
        event_q = event_q.filter(models.Event.source == source)

    for ev in event_q.order_by(models.Event.created_at.desc()).limit(limit).all():
        items.append({
            "id": ev.id,
            "timeline_type": "event",
            "title": ev.title,
            "content": ev.description or "",
            "source": ev.source,
            "event_type": ev.event_type,
            "severity": ev.severity,
            "created_at": ev.created_at,
        })

    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items[:limit]


def _process_entities(db: Session, entry: models.Entry) -> None:
    try:
        from .entity_service import extract_entities
        found = extract_entities(entry.content)
        for entity in found:
            entity_name = entity["name"]
            entity_type = entity["entity_type"]
            entity = db.query(models.Entity).filter(
                models.Entity.name == entity_name
            ).first()
            if not entity:
                entity = models.Entity(name=entity_name, entity_type=entity_type)
                db.add(entity)
                db.flush()

            existing = db.query(models.EntryEntity).filter(
                models.EntryEntity.entry_id == entry.id,
                models.EntryEntity.entity_id == entity.id,
            ).first()
            if not existing:
                link = models.EntryEntity(entry_id=entry.id, entity_id=entity.id)
                db.add(link)
        db.commit()
    except Exception:
        pass
