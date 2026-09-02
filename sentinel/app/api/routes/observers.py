from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.api.dependencies import get_runtime

router = APIRouter(tags=["observers"])


@router.get("/observers")
async def list_observers(request: Request) -> dict[str, Any]:
    """Live status of every configured observer."""
    runtime = get_runtime(request)
    scheduler = runtime.scheduler
    if scheduler is None:
        return {"observers": [], "running": False}
    statuses = []
    for item in scheduler.statuses():
        statuses.append(
            {
                "name": item.name,
                "enabled": item.enabled,
                "running": item.running,
                "interval": item.interval,
                "timeout": item.timeout,
                "last_collect_at": (
                    item.last_collect_at.isoformat() if item.last_collect_at else None
                ),
                "last_error": item.last_error,
                "observations_count": item.observations_count,
            }
        )
    return {"observers": statuses, "running": scheduler.running}
