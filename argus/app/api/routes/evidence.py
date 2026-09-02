from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.dependencies import get_repository, paginate

router = APIRouter(tags=["evidence"])


@router.get("/evidence")
async def list_evidence(
    request: Request,
    source: str | None = None,
    source_type: str | None = None,
    q: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Collected content items (the evidence store)."""
    repository = get_repository(request)
    items, total = await repository.list_content(
        source=source,
        source_type=source_type,
        q=q,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [item.model_dump(mode="json") for item in items],
        **paginate(total, limit, offset),
    }


@router.get("/evidence/{content_id}")
async def get_evidence(content_id: UUID, request: Request) -> dict[str, Any]:
    """One evidence item."""
    repository = get_repository(request)
    item = await repository.get_content(content_id)
    if item is None:
        raise HTTPException(status_code=404, detail="evidence not found")
    return item.model_dump(mode="json")
