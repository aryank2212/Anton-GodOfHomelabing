from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.dependencies import get_repository, paginate

router = APIRouter(tags=["entities"])


@router.get("/entities")
async def list_entities(
    request: Request,
    kind: str | None = None,
    q: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Resolved knowledge-graph entities."""
    repository = get_repository(request)
    entities, total = await repository.list_entities(kind=kind, q=q, limit=limit, offset=offset)
    return {
        "items": [entity.model_dump(mode="json") for entity in entities],
        **paginate(total, limit, offset),
    }


@router.get("/entities/{entity_id}")
async def get_entity(entity_id: UUID, request: Request) -> dict[str, Any]:
    """One entity plus its relations."""
    repository = get_repository(request)
    entity = await repository.get_entity(entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="entity not found")
    relations, _ = await repository.list_relations(entity_id=entity_id)
    return {
        **entity.model_dump(mode="json"),
        "relations": [relation.model_dump(mode="json") for relation in relations],
    }
