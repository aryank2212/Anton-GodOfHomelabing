from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas, auth
from ..services.memory_service import (
    create_memory,
    get_memory,
    search_memories,
    semantic_search,
    delete_memory,
    store_observation,
    get_memory_timeline,
)
from ..services.entity_service import extract_entities
from ..services.embedding_service import generate_embedding, is_available as embeddings_available
from ..services.audit_service import record_audit
from ..services.queue_service import queue

router = APIRouter(prefix="/api/memory", tags=["Memory"])


@router.post("")
def api_create_memory(
    data: schemas.EntryCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent"])

    data_dict = data.model_dump()
    if user:
        data_dict["user_id"] = user.id
    entry = create_memory(db, data_dict)

    queue.enqueue("embedding", {"entry_id": entry.id}, priority=1)
    queue.enqueue("entity_extraction", {"entry_id": entry.id}, priority=1)

    record_audit(
        db, "create", "user" if user else "agent",
        actor_id=user.id if user else None,
        actor_name=user.username if user else None,
        resource_type="memory", resource_id=entry.id,
    )

    return {
        "id": entry.id,
        "title": entry.title,
        "content": entry.content[:200],
        "entry_type": entry.entry_type,
        "source": entry.source,
        "visibility": entry.visibility,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


@router.get("/search")
def api_search_memories(
    request: Request,
    q: str = Query(""),
    source: str = Query(None),
    entry_type: str = Query(None),
    visibility: str = Query(None),
    mood: str = Query(None),
    tag: str = Query(None),
    semantic: bool = Query(False),
    limit: int = Query(20, le=200),
    offset: int = Query(0),
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)

    if semantic and q and embeddings_available():
        results = semantic_search(db, q, limit=limit, user=user)
    else:
        results = search_memories(
            db, q=q, source=source, entry_type=entry_type,
            visibility=visibility, mood=mood, tag=tag,
            limit=limit, offset=offset, user=user,
        )

    record_audit(
        db, "search", "user" if user else "anonymous",
        actor_id=user.id if user else None,
        resource_type="memory",
        details={"query": q, "semantic": semantic, "results": len(results)},
    )

    return results


@router.get("/timeline")
def api_memory_timeline(
    request: Request,
    source: str = Query(None),
    entry_type: str = Query(None),
    since: str = Query(None),
    limit: int = Query(50),
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(400, "Invalid datetime format. Use ISO format.")

    return get_memory_timeline(db, since=since_dt, source=source, entry_type=entry_type, limit=limit, user=user)


@router.get("/{memory_id}")
def api_get_memory(
    memory_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    entry = get_memory(db, memory_id)
    if not entry:
        raise HTTPException(404, "Memory not found")

    if not auth.check_visibility(entry.visibility, user, entry.user_id):
        raise HTTPException(403, "Access denied")

    return {
        "id": entry.id,
        "title": entry.title,
        "content": entry.content,
        "entry_type": entry.entry_type,
        "tags": entry.tags,
        "mood": entry.mood,
        "source": entry.source,
        "visibility": entry.visibility,
        "summary": entry.summary,
        "metadata_json": entry.metadata_json,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
    }


@router.delete("/{memory_id}")
def api_delete_memory(
    memory_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent"])

    entry = get_memory(db, memory_id)
    if not entry:
        raise HTTPException(404, "Memory not found")

    if not auth.check_visibility(entry.visibility, user, entry.user_id):
        raise HTTPException(403, "Access denied")

    if user and user.role != "admin" and entry.user_id != user.id:
        raise HTTPException(403, "Cannot delete another user's memory")

    if not delete_memory(db, memory_id):
        raise HTTPException(404, "Memory not found")

    record_audit(
        db, "delete", "user" if user else "agent",
        actor_id=user.id if user else None,
        resource_type="memory", resource_id=memory_id,
    )
    return {"status": "deleted", "id": memory_id}


@router.post("/reflection")
def api_create_reflection(
    data: schemas.EntryCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent"])

    entry = create_memory(db, {
        "title": data.title or f"Reflection - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "content": data.content,
        "entry_type": "Journal",
        "source": "memory",
        "visibility": data.visibility,
        "user_id": user.id if user else None,
    })

    record_audit(
        db, "create", "user" if user else "agent",
        actor_id=user.id if user else None,
        resource_type="reflection", resource_id=entry.id,
    )

    return {
        "id": entry.id,
        "title": entry.title,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


@router.post("/entity")
def api_store_entity(
    request: Request,
    name: str = Query(...),
    entity_type: str = Query("unknown"),
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent"])

    entity = db.query(models.Entity).filter(models.Entity.name == name).first()
    if not entity:
        entity = models.Entity(name=name, entity_type=entity_type)
        db.add(entity)
        db.commit()
        db.refresh(entity)

    return {"id": entity.id, "name": entity.name, "entity_type": entity.entity_type}


@router.post("/embed")
def api_generate_embedding(
    request: Request,
    entry_id: int = Query(...),
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent"])

    if not embeddings_available():
        raise HTTPException(503, "Embeddings not available. Install sentence-transformers.")

    entry = db.query(models.Entry).filter(models.Entry.id == entry_id).first()
    if not entry:
        raise HTTPException(404, "Entry not found")

    text = f"{entry.title or ''} {entry.content}"
    emb = generate_embedding(text)
    if not emb:
        raise HTTPException(500, "Failed to generate embedding")

    entry.embedding = emb
    db.commit()

    record_audit(
        db, "update", "user" if user else "agent",
        actor_id=user.id if user else None,
        resource_type="embedding", resource_id=entry_id,
    )

    return {"status": "embedded", "id": entry_id}


@router.post("/observation")
def api_store_observation(
    request: Request,
    content: str = Query(...),
    title: str = Query(None),
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent"])

    entry = store_observation(db, content, title, user_id=user.id if user else None)

    record_audit(
        db, "create", "user" if user else "agent",
        actor_id=user.id if user else None,
        resource_type="observation", resource_id=entry.id,
    )

    return {
        "id": entry.id,
        "title": entry.title,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }
