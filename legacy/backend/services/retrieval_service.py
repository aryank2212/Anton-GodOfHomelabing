import json
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_
from .. import models
from ..auth import check_visibility
from ..utils.tags import parse_tags
from .embedding_service import (
    generate_embedding,
    cosine_similarity,
    parse_embedding,
)


def similar_memories(
    entry_id: int,
    db: Session,
    limit: int = 5,
    user: Optional[models.User] = None,
) -> list[dict]:

    entry = db.query(models.Entry).filter(models.Entry.id == entry_id).first()
    if not entry or not entry.embedding:
        return []

    emb = parse_embedding(entry.embedding)
    if emb is None:
        return []

    entries = db.query(models.Entry).filter(
        models.Entry.id != entry_id,
        models.Entry.embedding.isnot(None),
    ).all()

    scored = []
    for other in entries:
        if not check_visibility(other.visibility, user, other.user_id):
            continue
        other_emb = parse_embedding(other.embedding)
        if other_emb is None:
            continue
        sim = cosine_similarity(emb, other_emb)
        if sim > 0.3:
            scored.append((other, sim))

    scored.sort(key=lambda x: x[1], reverse=True)

    result = []
    for other, sim in scored[:limit]:
        result.append({
            "id": other.id,
            "title": other.title or "Untitled Memory",
            "similarity": round(sim, 3),
            "created_at": other.created_at.isoformat() if other.created_at else None,
        })

    return result


def related_entries(
    entry_id: int,
    db: Session,
    user: Optional[models.User] = None,
) -> list[models.Entry]:

    entry = db.query(models.Entry).filter(models.Entry.id == entry_id).first()
    if not entry:
        return []

    entry_tags = set(parse_tags(entry.tags))

    entry_entity_ids = [
        ee.entity_id
        for ee in db.query(models.EntryEntity)
        .filter(models.EntryEntity.entry_id == entry_id)
        .all()
    ]

    candidates = db.query(models.Entry).filter(models.Entry.id != entry_id).all()

    scored = []
    for c in candidates:
        if not check_visibility(c.visibility, user, c.user_id):
            continue
        score = 0

        c_tags = set(parse_tags(c.tags))
        shared_tags = entry_tags & c_tags
        score += len(shared_tags) * 2

        c_entity_ids = [
            ee.entity_id
            for ee in db.query(models.EntryEntity)
            .filter(models.EntryEntity.entry_id == c.id)
            .all()
        ]
        shared_entities = set(entry_entity_ids) & set(c_entity_ids)
        score += len(shared_entities) * 3

        if score > 0:
            scored.append((c, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [s[0] for s in scored[:5]]


def semantic_search(
    query: str,
    db: Session,
    limit: int = 20,
    user: Optional[models.User] = None,
) -> list[dict]:

    keyword_results = db.query(models.Entry).filter(
        or_(
            models.Entry.title.contains(query),
            models.Entry.content.contains(query),
            models.Entry.tags.contains(query),
            models.Entry.mood.contains(query),
        )
    ).order_by(models.Entry.created_at.desc()).limit(limit).all()

    query_embedding = generate_embedding(query)
    if query_embedding is None:
        return [
            {
                "id": e.id,
                "title": e.title or "Untitled Memory",
                "score": 1.0,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in keyword_results
            if check_visibility(e.visibility, user, e.user_id)
        ]

    qe = json.loads(query_embedding)

    entries = db.query(models.Entry).filter(
        models.Entry.embedding.isnot(None)
    ).all()

    scored = []
    for e in entries:
        if not check_visibility(e.visibility, user, e.user_id):
            continue
        ee = parse_embedding(e.embedding)
        if ee is None:
            continue
        sim = cosine_similarity(qe, ee)
        if sim > 0.2:
            scored.append((e, sim))

    scored.sort(key=lambda x: x[1], reverse=True)

    seen_ids = set()
    result = []

    for e, sim in scored:
        if len(result) >= limit:
            break
        if e.id in seen_ids:
            continue
        seen_ids.add(e.id)
        result.append({
            "id": e.id,
            "title": e.title or "Untitled Memory",
            "score": round(sim, 3),
            "created_at": e.created_at.isoformat() if e.created_at else None,
        })

    for e in keyword_results:
        if len(result) >= limit:
            break
        if e.id in seen_ids:
            continue
        seen_ids.add(e.id)
        result.append({
            "id": e.id,
            "title": e.title or "Untitled Memory",
            "score": 1.0,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        })

    return result
