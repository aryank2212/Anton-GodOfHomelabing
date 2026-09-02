from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.models.incident import Incident, IncidentList

router = APIRouter(tags=["incidents"])


@router.get("/incidents", response_model=IncidentList)
async def list_incidents(
    request: Request,
    component: str | None = Query(default=None),
    status_: str | None = Query(default=None, alias="status"),
    severity: str | None = Query(default=None),
    failure_type: str | None = Query(default=None, alias="failureType"),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List incidents, newest first, with optional filters and pagination."""
    incidents = request.app.state.incidents
    items, total = await incidents.search(
        component=component,
        status=status_,
        severity=severity,
        failure_type=failure_type,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/incidents/{incident_id}", response_model=Incident)
async def get_incident(incident_id: str, request: Request) -> Incident:
    """Return a single incident with its recovery timeline."""
    incidents = request.app.state.incidents
    incident = await incidents.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")
    return incident


@router.get("/incidents/{incident_id}/timeline", response_model=list[dict[str, object]])
async def get_incident_timeline(incident_id: str, request: Request) -> list[dict[str, object]]:
    """Return the recovery timeline of an incident."""
    incidents = request.app.state.incidents
    incident = await incidents.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")
    return await incidents.timeline(incident_id)
