from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.api.dependencies import get_runtime

router = APIRouter(tags=["sources"])


@router.get("/sources")
async def list_sources(request: Request) -> dict[str, Any]:
    """Live health of every collector's ingest loop."""
    runtime = get_runtime(request)
    statuses = runtime.scheduler.statuses() if runtime.scheduler is not None else []
    return {
        "running": bool(runtime.scheduler and runtime.scheduler.running),
        "started_at": (
            runtime.scheduler.started_at.isoformat()
            if runtime.scheduler and runtime.scheduler.started_at
            else None
        ),
        "items": [
            {
                "name": status.name,
                "enabled": status.enabled,
                "interval": status.interval,
                "timeout": status.timeout,
                "running": status.running,
                "last_collect_at": (
                    status.last_collect_at.isoformat() if status.last_collect_at else None
                ),
                "last_error": status.last_error,
                "items_count": status.items_count,
                "consecutive_failures": status.consecutive_failures,
                "degraded": status.degraded,
                "backoff": status.backoff,
                "last_success_at": (
                    status.last_success_at.isoformat() if status.last_success_at else None
                ),
                "next_run_at": (
                    status.next_run_at.isoformat() if status.next_run_at else None
                ),
            }
            for status in statuses
        ],
    }
