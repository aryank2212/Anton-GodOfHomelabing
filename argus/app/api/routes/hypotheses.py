from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.dependencies import get_repository, paginate

router = APIRouter(tags=["hypotheses"])


@router.get("/hypotheses")
async def list_hypotheses(
    request: Request,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Generated hypotheses (rule-based and Oracle-generated)."""
    repository = get_repository(request)
    hypotheses, total = await repository.list_hypotheses(status=status, limit=limit, offset=offset)
    return {
        "items": [hypothesis.model_dump(mode="json") for hypothesis in hypotheses],
        **paginate(total, limit, offset),
    }


@router.get("/hypotheses/{hypothesis_id}")
async def get_hypothesis(hypothesis_id: UUID, request: Request) -> dict[str, Any]:
    repository = get_repository(request)
    hypothesis = await repository.get_hypothesis(hypothesis_id)
    if hypothesis is None:
        raise HTTPException(status_code=404, detail="hypothesis not found")
    return hypothesis.model_dump(mode="json")
