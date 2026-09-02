from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    changes,
    dots,
    entities,
    evidence,
    graph,
    health,
    hypotheses,
    reports,
    research,
    sources,
)

api_router = APIRouter(prefix="/v1")

for module in (
    health,
    evidence,
    entities,
    graph,
    sources,
    changes,
    hypotheses,
    reports,
    dots,
    research,
):
    api_router.include_router(module.router)
