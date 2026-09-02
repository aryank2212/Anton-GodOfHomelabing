from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import List

from .. import models, schemas, auth
from ..database import get_db
from ..services import extract_entities
from ..services.audit_service import record_audit
from ..services.queue_service import queue

router = APIRouter(prefix="/api/entries", tags=["Entries"])


def _compute_entry_content(entry: models.Entry) -> list:
    text = f"{entry.title or ''}\n{entry.content}"
    return extract_entities(text)


def _apply_entry_content(db: Session, entry: models.Entry, entities):
    for ent in entities:
        existing = db.query(models.Entity).filter(
            models.Entity.name == ent["name"],
            models.Entity.entity_type == ent["entity_type"],
        ).first()
        if not existing:
            existing = models.Entity(
                name=ent["name"],
                entity_type=ent["entity_type"],
            )
            db.add(existing)
            db.flush()

        link = db.query(models.EntryEntity).filter(
            models.EntryEntity.entry_id == entry.id,
            models.EntryEntity.entity_id == existing.id,
        ).first()
        if not link:
            db.add(models.EntryEntity(entry_id=entry.id, entity_id=existing.id))

    db.flush()


@router.post("/", response_model=schemas.EntryResponse)
def create_entry(
    entry: schemas.EntryCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    auth.enforce_role(user, ["admin", "user", "agent"])

    db_entry = models.Entry(**entry.model_dump())
    if user:
        db_entry.user_id = user.id
    entities = _compute_entry_content(db_entry)
    db.add(db_entry)
    db.flush()
    _apply_entry_content(db, db_entry, entities)
    db.commit()
    db.refresh(db_entry)

    record_audit(
        db, "create", "user" if user else "anonymous",
        actor_id=user.id if user else None,
        actor_name=user.username if user else None,
        resource_type="entry", resource_id=db_entry.id,
        details={"entry_type": db_entry.entry_type, "visibility": db_entry.visibility},
    )
    queue.enqueue("embedding", {"entry_id": db_entry.id}, priority=1)
    return db_entry


@router.get("/", response_model=List[schemas.EntrySummary])
def read_entries(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)

    query = db.query(models.Entry)

    if user and user.role != "admin":
        from sqlalchemy import or_
        query = query.filter(
            or_(
                models.Entry.visibility == "public",
                models.Entry.user_id == user.id,
            )
        )
    elif not user:
        query = query.filter(models.Entry.visibility == "public")

    entries = query.order_by(models.Entry.created_at.desc()).offset(skip).limit(limit).all()
    return entries


@router.get("/{entry_id}", response_model=schemas.EntryResponse)
def read_entry(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    entry = db.query(models.Entry).filter(models.Entry.id == entry_id).first()

    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")

    if not auth.check_visibility(entry.visibility, user, entry.user_id):
        raise HTTPException(403, "Access denied")

    return entry


@router.put("/{entry_id}", response_model=schemas.EntryResponse)
def update_entry(
    entry_id: int,
    entry: schemas.EntryUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    db_entry = db.query(models.Entry).filter(models.Entry.id == entry_id).first()

    if db_entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")

    if not auth.check_visibility(db_entry.visibility, user, db_entry.user_id):
        raise HTTPException(403, "Access denied")

    if user and user.role not in ("admin",) and db_entry.user_id != user.id:
        raise HTTPException(403, "Cannot edit another user's entry")

    for key, value in entry.model_dump(exclude_unset=True).items():
        setattr(db_entry, key, value)

    entities = _compute_entry_content(db_entry)
    db.flush()
    _apply_entry_content(db, db_entry, entities)
    db.commit()
    db.refresh(db_entry)

    record_audit(
        db, "update", "user" if user else "anonymous",
        actor_id=user.id if user else None,
        resource_type="entry", resource_id=entry_id,
    )
    queue.enqueue("embedding", {"entry_id": entry_id}, priority=1)
    return db_entry


@router.delete("/{entry_id}")
def delete_entry(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = auth.get_current_user(request, db)
    db_entry = db.query(models.Entry).filter(models.Entry.id == entry_id).first()

    if db_entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")

    if not auth.check_visibility(db_entry.visibility, user, db_entry.user_id):
        raise HTTPException(403, "Access denied")

    if user and user.role not in ("admin",) and db_entry.user_id != user.id:
        raise HTTPException(403, "Cannot delete another user's entry")

    record_audit(
        db, "delete", "user" if user else "anonymous",
        actor_id=user.id if user else None,
        resource_type="entry", resource_id=entry_id,
    )

    db.query(models.EntryEntity).filter(models.EntryEntity.entry_id == entry_id).delete()
    db.query(models.CollectionEntry).filter(models.CollectionEntry.entry_id == entry_id).delete()
    db.delete(db_entry)
    db.commit()
    return {"ok": True}
