from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.api.dependencies import get_repository, paginate
from app.models.observation import Observation
from app.models.situation import Situation

router = APIRouter(tags=["situations"])


class SituationList(BaseModel):
    items: list[Situation]
    pagination: dict[str, int | None]


@router.get("/situations", response_model=SituationList)
async def list_situations(
    request: Request,
    rule_id: str | None = Query(default=None),
    type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List situations with filters and pagination."""
    repository = get_repository(request)
    items, total = await repository.list_situations(
        rule_id=rule_id,
        type=type,
        status=status,
        severity=severity,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "pagination": paginate(total, limit, offset)}


@router.get("/situations/{situation_id}", response_model=Situation)
async def get_situation(situation_id: str, request: Request) -> Situation:
    """Return a single situation by id."""
    from uuid import UUID

    repository = get_repository(request)
    try:
        parsed = UUID(situation_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid situation id"
        ) from exc
    situation = await repository.get_situation(parsed)
    if situation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="situation not found")
    return situation


@router.get("/situations/{situation_id}/observations", response_model=list[Observation])
async def situation_observations(situation_id: str, request: Request) -> list[Observation]:
    """Return the observations a situation was derived from."""
    from uuid import UUID

    repository = get_repository(request)
    try:
        parsed = UUID(situation_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid situation id"
        ) from exc
    situation = await repository.get_situation(parsed)
    if situation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="situation not found")
    observations: list[Observation] = []
    for observation_id in situation.derived_from:
        observation = await repository.get_observation(observation_id)
        if observation is not None:
            observations.append(observation)
    return observations
