"""REST API for on-demand dot-matching investigations.

POST /v1/dots queues a new investigation; the runtime's serial background
worker executes it (search -> compare -> narrow -> synthesise). Clients poll
GET /v1/dots/{run_id} for progress and fetch the finished intelligence report
once the run is completed.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.api.auth import require_command_token
from app.api.dependencies import get_repository, get_runtime, paginate
from app.dots.engine import VALID_PROVIDERS
from app.models.base import utcnow
from app.models.dots import DotRunStatus, DotWatch

router = APIRouter(tags=["dots"])

__valid_statuses__ = frozenset(status.value for status in DotRunStatus)


class DotStartRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=512)
    iterations: int | None = Field(default=None, ge=1, le=30)
    providers: list[str] | None = None
    queries_per_round: int | None = Field(default=None, ge=1, le=8)
    max_items_per_round: int | None = Field(default=None, ge=1, le=50)


class DotWatchRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=512)
    iterations: int | None = Field(default=None, ge=1, le=30)
    providers: list[str] | None = None
    queries_per_round: int | None = Field(default=None, ge=1, le=8)
    max_items_per_round: int | None = Field(default=None, ge=1, le=50)
    interval_hours: float = Field(default=24.0, ge=0.1, le=8760)


class DotWatchPatch(BaseModel):
    enabled: bool | None = None
    iterations: int | None = Field(default=None, ge=1, le=30)
    providers: list[str] | None = None
    queries_per_round: int | None = Field(default=None, ge=1, le=8)
    max_items_per_round: int | None = Field(default=None, ge=1, le=50)
    interval_hours: float | None = Field(default=None, ge=0.1, le=8760)


@router.post("/dots", status_code=status.HTTP_202_ACCEPTED)
async def start_dots(
    payload: DotStartRequest,
    request: Request,
    _auth: None = Depends(require_command_token),
) -> dict[str, Any]:
    """Queue an on-demand internet investigation."""
    runtime = get_runtime(request)
    if runtime.dots is None:
        raise HTTPException(status_code=503, detail="dots engine is disabled")
    try:
        run = await runtime.dots.enqueue(
            payload.topic,
            iterations=payload.iterations,
            providers=payload.providers,
            queries_per_round=payload.queries_per_round,
            max_items_per_round=payload.max_items_per_round,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "dot_run_id": run.dot_run_id,
        "topic": run.topic,
        "status": run.status.value,
        "iterations_target": run.iterations_target,
    }


@router.get("/dots")
async def list_dots(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """All dot investigations, newest first."""
    if status_filter is not None and status_filter not in __valid_statuses__:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of: {', '.join(sorted(__valid_statuses__))}",
        )
    repository = get_repository(request)
    dots, total = await repository.list_dot_runs(
        status=status_filter, limit=limit, offset=offset
    )
    return {
        "items": [dot.model_dump(mode="json") for dot in dots],
        **paginate(total, limit, offset),
    }


# ------------------------------------------------------------ dot watches
# NOTE: watch routes must stay above the generic /dots/{dot_run_id} routes or
# FastAPI would shadow the literal "watches" segment with the UUID path param.


def _validate_providers(providers: list[str] | None) -> list[str]:
    if providers is None:
        provider_list = list(VALID_PROVIDERS)
    else:
        unknown = [name for name in providers if name not in VALID_PROVIDERS]
        if unknown:
            raise HTTPException(
                status_code=422, detail=f"unknown providers: {', '.join(unknown)}"
            )
        provider_list = list(providers)
    return provider_list


@router.get("/dots/watches")
async def list_dot_watches(request: Request) -> dict[str, Any]:
    """All scheduled dot investigations, oldest first."""
    repository = get_repository(request)
    watches = await repository.list_dot_watches()
    return {"items": [watch.model_dump(mode="json") for watch in watches], "total": len(watches)}


@router.post("/dots/watches", status_code=status.HTTP_201_CREATED)
async def create_dot_watch(
    payload: DotWatchRequest,
    request: Request,
    _auth: None = Depends(require_command_token),
) -> dict[str, Any]:
    """Register a topic that re-runs on a schedule."""
    topic = payload.topic.strip()
    if not topic:
        raise HTTPException(status_code=422, detail="topic must not be empty")
    watch = DotWatch(
        topic=topic,
        iterations=payload.iterations or 12,
        providers=_validate_providers(payload.providers),
        queries_per_round=payload.queries_per_round or 3,
        max_items_per_round=payload.max_items_per_round or 12,
        interval_hours=payload.interval_hours,
    )
    repository = get_repository(request)
    await repository.save_dot_watch(watch)
    return watch.model_dump(mode="json")


@router.get("/dots/watches/{dot_watch_id}")
async def get_dot_watch(dot_watch_id: UUID, request: Request) -> dict[str, Any]:
    """One scheduled dot investigation."""
    repository = get_repository(request)
    watch = await repository.get_dot_watch(dot_watch_id)
    if watch is None:
        raise HTTPException(status_code=404, detail="dot watch not found")
    return watch.model_dump(mode="json")


@router.patch("/dots/watches/{dot_watch_id}")
async def patch_dot_watch(
    dot_watch_id: UUID,
    payload: DotWatchPatch,
    request: Request,
    _auth: None = Depends(require_command_token),
) -> dict[str, Any]:
    """Update schedule fields of a dot watch."""
    repository = get_repository(request)
    watch = await repository.get_dot_watch(dot_watch_id)
    if watch is None:
        raise HTTPException(status_code=404, detail="dot watch not found")
    updates = payload.model_dump(exclude_unset=True)
    if "providers" in updates:
        updates["providers"] = _validate_providers(updates["providers"])
    for key, value in updates.items():
        setattr(watch, key, value)
    watch.updated_at = utcnow()
    await repository.save_dot_watch(watch)
    return watch.model_dump(mode="json")


@router.delete("/dots/watches/{dot_watch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_dot_watch(
    dot_watch_id: UUID,
    request: Request,
    _auth: None = Depends(require_command_token),
) -> None:
    """Delete a dot watch."""
    repository = get_repository(request)
    if not await repository.delete_dot_watch(dot_watch_id):
        raise HTTPException(status_code=404, detail="dot watch not found")


@router.post("/dots/watches/{dot_watch_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_dot_watch_now(
    dot_watch_id: UUID,
    request: Request,
    _auth: None = Depends(require_command_token),
) -> dict[str, Any]:
    """Queue a watch topic immediately, outside its normal schedule."""
    repository = get_repository(request)
    watch = await repository.get_dot_watch(dot_watch_id)
    if watch is None:
        raise HTTPException(status_code=404, detail="dot watch not found")
    runtime = get_runtime(request)
    if runtime.dots is None:
        raise HTTPException(status_code=503, detail="dots engine is disabled")
    try:
        run = await runtime.dots.enqueue(
            watch.topic,
            iterations=watch.iterations,
            providers=watch.providers,
            queries_per_round=watch.queries_per_round,
            max_items_per_round=watch.max_items_per_round,
            watch_id=str(watch.dot_watch_id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await repository.mark_dot_watch_queued(
        watch.dot_watch_id, utcnow() + timedelta(hours=watch.interval_hours)
    )
    return {
        "dot_run_id": run.dot_run_id,
        "topic": run.topic,
        "status": run.status.value,
        "iterations_target": run.iterations_target,
    }


@router.get("/dots/{dot_run_id}")
async def get_dot_run(dot_run_id: UUID, request: Request) -> dict[str, Any]:
    """One dot investigation with its progress and reasoning history."""
    repository = get_repository(request)
    dot_run = await repository.get_dot_run(dot_run_id)
    if dot_run is None:
        raise HTTPException(status_code=404, detail="dot run not found")
    return dot_run.model_dump(mode="json")


@router.get("/dots/{dot_run_id}/batches")
async def get_dot_batches(
    dot_run_id: UUID,
    request: Request,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Per-round search batches of a dot investigation."""
    repository = get_repository(request)
    batches, total = await repository.list_dot_batches(
        dot_run_id=dot_run_id, limit=limit, offset=offset
    )
    return {
        "items": [batch.model_dump(mode="json") for batch in batches],
        **paginate(total, limit, offset),
    }


@router.get("/dots/{dot_run_id}/report")
async def get_dot_report(dot_run_id: UUID, request: Request) -> dict[str, Any]:
    """The intelligence report produced by a completed dot run."""
    repository = get_repository(request)
    dot_run = await repository.get_dot_run(dot_run_id)
    if dot_run is None:
        raise HTTPException(status_code=404, detail="dot run not found")
    if dot_run.report_id is None:
        raise HTTPException(status_code=404, detail="dot run has no report yet")
    report = await repository.get_report(dot_run.report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="report not found")
    return report.model_dump(mode="json")


@router.post("/dots/{dot_run_id}/cancel")
async def cancel_dot_run(
    dot_run_id: UUID,
    request: Request,
    _auth: None = Depends(require_command_token),
) -> dict[str, Any]:
    """Ask the worker to stop an investigation at the next round boundary."""
    repository = get_repository(request)
    dot_run = await repository.get_dot_run(dot_run_id)
    if dot_run is None:
        raise HTTPException(status_code=404, detail="dot run not found")
    if dot_run.status in (DotRunStatus.COMPLETED, DotRunStatus.FAILED, DotRunStatus.CANCELLED):
        raise HTTPException(status_code=409, detail=f"run is already {dot_run.status.value}")
    dot_run.status = DotRunStatus.CANCELLED
    await repository.save_dot_run(dot_run)
    return {"dot_run_id": dot_run_id, "status": "cancelled"}
