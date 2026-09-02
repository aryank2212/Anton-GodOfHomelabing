from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.api.dependencies import get_repository, paginate
from app.models.device import Device

router = APIRouter(tags=["devices"])


class DeviceList(BaseModel):
    items: list[Device]
    pagination: dict[str, int | None]


@router.get("/devices", response_model=DeviceList)
async def list_devices(
    request: Request,
    known: bool | None = Query(default=None),
    online: bool | None = Query(default=None),
    category: str | None = Query(default=None),
    q: str | None = Query(default=None, description="search name / mac / ip / vendor / owner"),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Device inventory with filters, search and pagination."""
    repository = get_repository(request)
    items, total = await repository.list_devices(
        known=known,
        online=online,
        category=category,
        q=q,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "pagination": paginate(total, limit, offset)}


@router.get("/devices/{device_key}", response_model=Device)
async def get_device(device_key: str, request: Request) -> Device:
    """Return a single device by key."""
    repository = get_repository(request)
    device = await repository.get_device(device_key)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    return device


@router.get("/devices/{device_key}/history")
async def device_history(
    device_key: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Lifecycle history (joined / left / seen) for a device."""
    repository = get_repository(request)
    if await repository.get_device(device_key) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    items, total = await repository.list_device_events(
        device_key=device_key, limit=limit, offset=offset
    )
    return {"items": items, "pagination": paginate(total, limit, offset)}
