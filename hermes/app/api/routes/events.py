from __future__ import annotations

from fastapi import APIRouter, Query, Request, status

from app.models.event import EventCreate, EventList, EventResponse, Severity
from app.services.event_service import EventService

router = APIRouter(tags=["events"])


@router.post(
    "/event",
    response_model=EventResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a new event",
)
async def create_event(payload: EventCreate, request: Request) -> EventResponse:
    """Persist the event and queue it for rule evaluation and delivery.

    Returns immediately (202) with the event id; notification dispatch happens
    asynchronously in the background worker.
    """
    service = EventService(request.app.state)
    return await service.create(payload)


@router.get("/events", response_model=EventList, summary="List stored events")
async def list_events(
    request: Request,
    module: str | None = Query(default=None, description="Filter by module"),
    type_: str | None = Query(default=None, alias="type", description="Filter by type"),
    severity: Severity | None = Query(default=None, description="Filter by severity"),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> EventList:
    """Return stored events, newest first, with optional filters and pagination."""
    service = EventService(request.app.state)
    return await service.list_events(
        module=module,
        type_=type_,
        severity=severity,
        limit=limit,
        offset=offset,
    )
