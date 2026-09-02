from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from app.api.dependencies import get_repository, paginate

router = APIRouter(tags=["changes"])


@router.get("/changes")
async def list_changes(
    request: Request,
    change_type: str | None = None,
    severity: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Detected changes in tracked content (edits, takedowns, adds)."""
    repository = get_repository(request)
    changes, total = await repository.list_changes(
        change_type=change_type, severity=severity, limit=limit, offset=offset
    )
    return {
        "items": [change.model_dump(mode="json") for change in changes],
        **paginate(total, limit, offset),
    }
