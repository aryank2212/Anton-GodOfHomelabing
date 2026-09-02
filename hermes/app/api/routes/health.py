from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter(tags=["system"])


@router.get("/health", response_model=dict[str, Any])
async def health(request: Request) -> JSONResponse:
    database = request.app.state.database
    db_ok = True
    try:
        async with database.session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    queue = request.app.state.queue
    bot = getattr(request.app.state, "bot", None)
    storm = getattr(request.app.state, "storm", None)
    oracle = getattr(request.app.state, "oracle", None)
    return JSONResponse(
        {
            "status": "ok" if db_ok else "degraded",
            "version": request.app.state.settings.version,
            "environment": request.app.state.settings.environment,
            "database": "ok" if db_ok else "error",
            "queue": {"size": queue.size()} if queue else None,
            "bot": {"enabled": bool(bot and bot.enabled)},
            "storm": {"enabled": bool(storm and storm.enabled)},
            "ai": {"enabled": bool(oracle and oracle.enabled)},
        }
    )
