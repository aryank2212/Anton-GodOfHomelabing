from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.api.dependencies import get_repository, paginate

router = APIRouter(tags=["graph"])


@router.get("/graph/relations")
async def list_relations(
    request: Request,
    entity_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Knowledge-graph edges between entities."""
    repository = get_repository(request)
    relations, total = await repository.list_relations(
        entity_id=entity_id, limit=limit, offset=offset
    )
    return {
        "items": [relation.model_dump(mode="json") for relation in relations],
        **paginate(total, limit, offset),
    }
