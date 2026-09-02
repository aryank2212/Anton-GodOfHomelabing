from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.api.dependencies import get_repository, get_runtime
from app.models.observation import utcnow

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Liveness/readiness of Sentinel itself."""
    runtime = get_runtime(request)
    repository = get_repository(request)
    settings = runtime.settings

    database_ok = True
    observations = None
    active_situations = None
    try:
        observations = await repository.count_observations()
        active_situations = await repository.count_situations("active")
    except Exception:  # noqa: BLE001 - report degraded, never crash
        database_ok = False

    scheduler = runtime.scheduler
    status = "ok" if database_ok else "degraded"

    return {
        "status": status,
        "version": settings.version,
        "environment": settings.environment,
        "database": "ok" if database_ok else "error",
        "uptime_seconds": round((utcnow() - runtime.started_at).total_seconds(), 1),
        "observations": observations,
        "situations": {"active": active_situations},
        "observers": {
            "enabled": bool(scheduler and scheduler.running),
            "count": len(runtime.observers),
        },
        "last_correlation_tick": (
            runtime.last_correlation_tick.isoformat() if runtime.last_correlation_tick else None
        ),
        "last_presence_tick": (
            runtime.last_presence_tick.isoformat() if runtime.last_presence_tick else None
        ),
        "presence": (
            runtime.presence.latest.status.value
            if runtime.presence and runtime.presence.latest
            else None
        ),
        "hermes": {
            "enabled": settings.hermes_enabled,
            "base_url": settings.hermes_base_url,
        },
    }
