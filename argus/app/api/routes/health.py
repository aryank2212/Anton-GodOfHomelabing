from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.api.dependencies import get_repository, get_runtime
from app.models.base import utcnow

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Liveness/readiness of Argus itself."""
    runtime = get_runtime(request)
    repository = get_repository(request)
    settings = runtime.settings

    database_ok = True
    evidence_count = None
    entity_count = None
    research_count = None
    try:
        evidence_count = await repository.count_content()
        entity_count = await repository.count_entities()
        research_count = await repository.count_research_sessions()
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
        "evidence": evidence_count,
        "entities": entity_count,
        "research_sessions": research_count,
        "collectors": {
            "enabled": bool(scheduler and scheduler.running),
            "count": len(runtime.collectors),
        },
        "last_intelligence_tick": (
            runtime.last_intelligence_tick.isoformat() if runtime.last_intelligence_tick else None
        ),
        "oracle": {
            "enabled": settings.oracle_enabled,
            "base_url": settings.oracle_base_url,
        },
        "hermes": {
            "enabled": settings.hermes_enabled,
            "base_url": settings.hermes_base_url,
        },
    }
