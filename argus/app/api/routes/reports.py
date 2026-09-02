from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.dependencies import get_repository, paginate

router = APIRouter(tags=["reports"])


@router.get("/reports")
async def list_reports(
    request: Request,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Published intelligence reports."""
    repository = get_repository(request)
    reports, total = await repository.list_reports(status=status, limit=limit, offset=offset)
    return {
        "items": [report.model_dump(mode="json") for report in reports],
        **paginate(total, limit, offset),
    }


@router.get("/reports/{report_id}")
async def get_report(report_id: UUID, request: Request) -> dict[str, Any]:
    repository = get_repository(request)
    report = await repository.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="report not found")
    return report.model_dump(mode="json")
