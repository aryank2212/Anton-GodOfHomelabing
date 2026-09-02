from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.logging import get_logger

router = APIRouter(tags=["system"])
log = get_logger(__name__)


@router.get("/health", response_model=dict[str, Any])
async def health(request: Request) -> JSONResponse:
    """Liveness/readiness of Phoenix itself."""
    database = request.app.state.database
    db_ok = True
    try:
        async with database.session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        db_ok = False
        log.error("health_database_error", extra={"error": str(exc)})

    scheduler = request.app.state.scheduler
    snapshot = request.app.state.snapshot
    settings = request.app.state.settings
    incidents = request.app.state.incidents

    open_incidents = 0
    if db_ok:
        open_incidents = await incidents.open_count()

    monitors = {}
    for name in scheduler.monitor_names():
        result = snapshot.result(name)
        monitors[name] = {
            "status": result.status if result else "pending",
            "ok": result.ok if result else None,
            "last_check": result.checked_at.isoformat() if result else None,
        }

    status = "ok" if db_ok and (not settings.scheduler_enabled or scheduler.running) else "degraded"
    return JSONResponse(
        {
            "status": status,
            "version": settings.version,
            "environment": settings.environment,
            "database": "ok" if db_ok else "error",
            "scheduler": {
                "enabled": settings.scheduler_enabled,
                "running": scheduler.running,
                "monitors": scheduler.monitor_count,
                "last_tick": scheduler.last_tick.isoformat() if scheduler.last_tick else None,
            },
            "monitors": monitors,
            "open_incidents": open_incidents,
            "hermes": {
                "enabled": settings.hermes_enabled,
                "base_url": settings.hermes_base_url,
            },
        }
    )
