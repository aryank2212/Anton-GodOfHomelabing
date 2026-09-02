from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from app.api.dependencies import get_repository, get_tracker, paginate
from app.models.device import PresenceState

router = APIRouter(tags=["presence"])


class PresenceList(BaseModel):
    items: list[PresenceState]
    pagination: dict[str, int | None]


@router.get("/presence")
async def get_presence(request: Request) -> dict[str, Any]:
    """Current presence state plus a live device snapshot."""
    repository = get_repository(request)
    tracker = get_tracker(request)
    state = await repository.latest_presence()
    devices = tracker.snapshot()
    online = [device for device in devices if device.online]
    known_online = [device for device in online if device.known]
    unknown_online = [device for device in online if not device.known]
    return {
        "state": state.model_dump(mode="json") if state else None,
        "devices": {
            "online": [device.display_name for device in known_online],
            "unknown": [device.display_name for device in unknown_online],
            "online_count": len(online),
            "known_count": len(known_online),
            "unknown_count": len(unknown_online),
        },
        "timestamp": state.timestamp if state else None,
    }


@router.get("/presence/history", response_model=PresenceList)
async def presence_history(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Presence history samples, newest first."""
    repository = get_repository(request)
    items, total = await repository.list_presence_samples(status=status, limit=limit, offset=offset)
    return {"items": items, "pagination": paginate(total, limit, offset)}
