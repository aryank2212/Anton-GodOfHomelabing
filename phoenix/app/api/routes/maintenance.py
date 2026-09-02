from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.services.maintenance import MaintenanceCreate

router = APIRouter(tags=["maintenance"])


@router.post("/maintenance", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_maintenance(payload: MaintenanceCreate, request: Request) -> dict:
    """Schedule a maintenance window for a component."""
    return await request.app.state.maintenance.create(payload)


@router.get("/maintenance", response_model=list[dict])
async def list_maintenance(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    """List the maintenance log, newest first."""
    return await request.app.state.maintenance.list(limit, offset)


@router.delete("/maintenance/{maintenance_id}", status_code=status.HTTP_204_NO_CONTENT)
async def close_maintenance(maintenance_id: int, request: Request) -> None:
    """Close an open maintenance window immediately."""
    closed = await request.app.state.maintenance.close(maintenance_id)
    if not closed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="maintenance entry not found"
        )
