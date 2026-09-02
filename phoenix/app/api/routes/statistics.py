from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["statistics"])


@router.get("/statistics", response_model=dict)
async def statistics(request: Request) -> dict:
    """Aggregated recovery statistics over the full incident history.

    These numbers feed later AI analysis on Oracle.
    """
    return await request.app.state.statistics.overview()
