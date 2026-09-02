from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_
from .. import models
from ..auth import check_visibility
from ..utils.tags import parse_tags
from .embedding_service import is_available, generate_embedding, parse_embedding, cosine_similarity


def build_rag_context(
    db: Session,
    query: str,
    max_entries: int = 5,
    max_events: int = 3,
    include_entities: bool = True,
    user: Optional[models.User] = None,
) -> str:
    context_sections = []

    if is_available():
        semantic_results = _semantic_retrieval(db, query, max_entries, user=user)
        if semantic_results:
            context_sections.append("## Directly Related Entries")
            for item in semantic_results:
                context_sections.append(
                    f"- [{item['entry_type']}] {item['title'] or 'Untitled'}: "
                    f"{item['content'][:400]} (relevance: {item['score']})"
                )

    keyword_results = _keyword_retrieval(db, query, max_entries, user=user)
    if keyword_results:
        context_sections.append("## Keyword Matches")
        for item in keyword_results:
            context_sections.append(
                f"- [{item['entry_type']}] {item['title'] or 'Untitled'}: "
                f"{item['content'][:400]}"
            )

    events = _retrieve_events(db, query, max_events)
    if events:
        context_sections.append("## Related System Events")
        for ev in events:
            context_sections.append(
                f"- [{ev.source.upper()}] {ev.title or ev.event_type}: "
                f"{ev.description or ''}"
            )

    if include_entities:
        entities = _retrieve_entities(db, query)
        if entities:
            context_sections.append("## Related Entities")
            for ent in entities:
                context_sections.append(f"- {ent.name} ({ent.entity_type})")

    return "\n\n".join(context_sections)


def _semantic_retrieval(
    db: Session, query: str, limit: int,
    user: Optional[models.User] = None,
) -> list[dict]:
    emb = generate_embedding(query)
    if not emb:
        return []
    vec = parse_embedding(emb)
    if not vec:
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
            sim = cosine_similarity(vec, ev)
            scored.append((sim, e))

    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for sim, e in scored[:limit]:
        results.append({
            "title": e.title,
            "content": e.content,
            "entry_type": e.entry_type,
            "score": round(sim, 3),
        })

    return results


def _keyword_retrieval(
    db: Session, query: str, limit: int,
    user: Optional[models.User] = None,
) -> list[dict]:
    words = [w.strip() for w in query.split() if len(w.strip()) > 2]
    if not words:
        return []

    filters = []
    for word in words:
        filters.append(models.Entry.content.contains(word))
        filters.append(models.Entry.title.contains(word))
        filters.append(models.Entry.tags.contains(word))

    entry_q = db.query(models.Entry).filter(or_(*filters))

    if user and user.role != "admin":
        entry_q = entry_q.filter(
            or_(
                models.Entry.visibility == "public",
                models.Entry.user_id == user.id,
            )
        )
    elif not user:
        entry_q = entry_q.filter(models.Entry.visibility == "public")

    entries = entry_q.order_by(models.Entry.created_at.desc()).limit(limit).all()

    return [
        {"title": e.title, "content": e.content, "entry_type": e.entry_type}
        for e in entries
    ]


def _retrieve_events(db: Session, query: str, limit: int) -> list:
    words = [w.strip() for w in query.split() if len(w.strip()) > 2]
    if not words:
        return []

    filters = []
    for word in words:
        filters.append(models.Event.title.contains(word))
        filters.append(models.Event.description.contains(word))
        filters.append(models.Event.event_type.contains(word))

    return db.query(models.Event).filter(
        or_(*filters)
    ).order_by(models.Event.created_at.desc()).limit(limit).all()


def _retrieve_entities(db: Session, query: str) -> list:
    words = [w.strip() for w in query.split() if len(w.strip()) > 2]
    if not words:
        return []

    filters = []
    for word in words:
        filters.append(models.Entity.name.contains(word))

    return db.query(models.Entity).filter(
        or_(*filters)
    ).limit(10).all()
