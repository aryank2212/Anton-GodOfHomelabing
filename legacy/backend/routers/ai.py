from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from typing import List

from .. import models, schemas, auth
from ..database import get_db
from ..services import (
    get_graph_data,
    similar_memories,
    related_entries,
    semantic_search,
    embeddings_available,
)
from ..services.audit_service import record_audit

router = APIRouter(prefix="/api/ai", tags=["AI"])


@router.get("/graph")
def graph_data(
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    return get_graph_data(db, user=user)


@router.get("/similar/{entry_id}")
def similar(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    return similar_memories(entry_id, db, user=user)


@router.get("/related/{entry_id}")
def related(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    entries = related_entries(entry_id, db, user=user)
    return [
        {
            "id": e.id,
            "title": e.title or "Untitled Memory",
            "entry_type": e.entry_type,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries
    ]


@router.get("/search")
def search(
    q: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    results = semantic_search(q, db, user=user)

    record_audit(
        db, "search", "user" if user else "anonymous",
        actor_id=user.id if user else None,
        resource_type="entry",
        details={"query": q, "results": len(results)},
    )
    return results


@router.get("/status")
def status():
    return {"embeddings_available": embeddings_available()}


@router.get("/entities", response_model=List[schemas.EntityResponse])
def list_entities(
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent", "read-only"])

    return db.query(models.Entity).order_by(models.Entity.name.asc()).all()


@router.get("/entities/{entity_id}")
def get_entity(
    entity_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent", "read-only"])

    entity = db.query(models.Entity).filter(models.Entity.id == entity_id).first()
    if not entity:
        return {"error": "Entity not found"}

    entry_links = db.query(models.EntryEntity).filter(
        models.EntryEntity.entity_id == entity_id
    ).all()
    entry_ids = [el.entry_id for el in entry_links]

    entries = []
    if entry_ids:
        entry_query = db.query(models.Entry).filter(
            models.Entry.id.in_(entry_ids)
        )
        if user and user.role != "admin":
            entry_query = entry_query.filter(
                models.Entry.visibility.in_(["public", "shared"]),
                models.Entry.user_id == user.id,
            )
        entries = entry_query.order_by(models.Entry.created_at.desc()).all()

    return {
        "entity": {
            "id": entity.id,
            "name": entity.name,
            "entity_type": entity.entity_type,
        },
        "entries": [
            {
                "id": e.id,
                "title": e.title or "Untitled Memory",
                "entry_type": e.entry_type,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in entries
        ],
    }


@router.get("/collections", response_model=List[schemas.CollectionResponse])
def list_collections(
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent", "read-only"])

    query = db.query(models.Collection)
    if user and user.role != "admin":
        query = query.filter(
            models.Collection.visibility.in_(["public", "shared"]),
            models.Collection.user_id == user.id,
        )
    return query.order_by(models.Collection.name.asc()).all()


@router.get("/collections/{collection_id}")
def get_collection(
    collection_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent", "read-only"])

    collection = db.query(models.Collection).filter(
        models.Collection.id == collection_id
    ).first()
    if not collection:
        return {"error": "Collection not found"}

    entry_ids = [
        ce.entry_id
        for ce in db.query(models.CollectionEntry)
        .filter(models.CollectionEntry.collection_id == collection_id)
        .all()
    ]

    entries = []
    if entry_ids:
        entry_query = db.query(models.Entry).filter(
            models.Entry.id.in_(entry_ids)
        )
        if user and user.role != "admin":
            entry_query = entry_query.filter(
                models.Entry.visibility.in_(["public", "shared"]),
                models.Entry.user_id == user.id,
            )
        entries = entry_query.order_by(models.Entry.created_at.desc()).all()

    return {
        "collection": {
            "id": collection.id,
            "name": collection.name,
            "description": collection.description,
        },
        "entries": [
            {
                "id": e.id,
                "title": e.title or "Untitled Memory",
                "entry_type": e.entry_type,
                "tags": e.tags,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in entries
        ],
    }


@router.get("/settings")
def get_settings(
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent", "read-only"])
    settings = db.query(models.Setting).all()
    return {s.key: s.value for s in settings}


@router.post("/settings")
async def save_settings(
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin"])

    body = await request.json()
    for key, value in body.items():
        setting = db.query(models.Setting).filter(models.Setting.key == key).first()
        if setting:
            setting.value = str(value)
        else:
            db.add(models.Setting(key=key, value=str(value)))
    db.commit()

    record_audit(
        db, "update", "user", user.id, user.username,
        resource_type="settings",
    )
    return {"ok": True}


@router.post("/collections/create")
def create_collection(
    request: Request,
    name: str,
    description: str = "",
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent"])

    collection = models.Collection(name=name, description=description or None)
    collection.user_id = user.id if user else None
    db.add(collection)
    db.flush()

    tag = name.lower()
    entry_query = db.query(models.Entry).filter(models.Entry.tags.contains(tag))
    if user and user.role != "admin":
        entry_query = entry_query.filter(
            models.Entry.user_id == user.id,
        )
    entries = entry_query.all()

    for e in entries:
        existing = db.query(models.CollectionEntry).filter(
            models.CollectionEntry.collection_id == collection.id,
            models.CollectionEntry.entry_id == e.id,
        ).first()
        if not existing:
            db.add(models.CollectionEntry(collection_id=collection.id, entry_id=e.id))

    db.commit()

    record_audit(
        db, "create", "user", user.id if user else None,
        resource_type="collection", resource_id=collection.id,
    )
    return {"id": collection.id, "name": collection.name}


@router.delete("/collections/{collection_id}")
def delete_collection(
    collection_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent"])

    collection = db.query(models.Collection).filter(
        models.Collection.id == collection_id
    ).first()
    if not collection:
        return {"error": "Collection not found"}

    if user and user.role != "admin" and collection.user_id != user.id:
        raise HTTPException(403, "Cannot delete another user's collection")

    db.query(models.CollectionEntry).filter(
        models.CollectionEntry.collection_id == collection_id
    ).delete()
    db.delete(collection)
    db.commit()

    record_audit(
        db, "delete", "user", user.id if user else None,
        resource_type="collection", resource_id=collection_id,
    )
    return {"ok": True}
