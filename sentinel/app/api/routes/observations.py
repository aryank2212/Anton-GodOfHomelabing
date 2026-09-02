from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.api.dependencies import get_repository, paginate
from app.models.observation import Observation

router = APIRouter(tags=["observations"])


class ObservationList(BaseModel):
    items: list[Observation]
    pagination: dict[str, int | None]


@router.get("/observations", response_model=ObservationList)
async def list_observations(
    request: Request,
    source: str | None = Query(default=None),
    category: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    object: str | None = Query(default=None),
    state: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List observations, newest first, with filters and pagination."""
    repository = get_repository(request)
    items, total = await repository.list_observations(
        source=source,
        category=category,
        severity=severity,
        object=object,
        state=state,
        tag=tag,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "pagination": paginate(total, limit, offset)}


@router.get("/observations/{observation_id}", response_model=Observation)
async def get_observation(observation_id: str, request: Request) -> Observation:
    """Return a single observation by id."""
    repository = get_repository(request)
    from uuid import UUID

    try:
        parsed = UUID(observation_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid observation id"
        ) from exc
    observation = await repository.get_observation(parsed)
    if observation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="observation not found")
    return observation
