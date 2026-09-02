"""REST API for research sessions.

POST /v1/research/sessions queues a goal-directed research question. The
research worker decomposes it into research angles, runs each as a dot
investigation, and writes a final synthesised report. Clients poll
GET /v1/research/sessions/{id} for progress.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.api.auth import require_command_token
from app.api.dependencies import get_repository, get_runtime, paginate
from app.models.base import utcnow
from app.models.research import (
    ResearchSession,
    ResearchSessionMode,
    ResearchSessionStatus,
    ResearchTarget,
)

router = APIRouter(prefix="/research", tags=["research"])

__valid_statuses__ = frozenset(value.value for value in ResearchSessionStatus)
__valid_modes__ = frozenset(value.value for value in ResearchSessionMode)


class ResearchSessionRequest(BaseModel):
    question: str = Field(default="", min_length=0, max_length=2000)
    context: str = Field(default="", max_length=4000)
    mode: ResearchSessionMode = ResearchSessionMode.SINGLE_PASS
    max_angles: int | None = Field(default=None, ge=1, le=6)
    target: ResearchTarget | None = None


def _fields(payload: ResearchSessionRequest, settings) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    question = payload.question.strip()
    if payload.target is not None and payload.target.has_any():
        metadata["target"] = payload.target.model_dump(mode="json")
        composed = payload.target.as_question()
        question = f"{question}\n{composed}".strip() if question else composed
    return {
        "question": question,
        "context": payload.context.strip(),
        "mode": payload.mode,
        "max_angles": payload.max_angles or settings.research_max_angles,
        "metadata": metadata,
    }


@router.post("/sessions", status_code=status.HTTP_202_ACCEPTED)
async def create_research_session(
    payload: ResearchSessionRequest,
    request: Request,
    _auth: None = Depends(require_command_token),
) -> dict[str, Any]:
    """Queue a goal-directed research question."""
    runtime = get_runtime(request)
    if runtime.research is None:
        raise HTTPException(status_code=503, detail="research worker is disabled")
    settings = runtime.settings
    fields = _fields(payload, settings)
    if not fields["question"].strip():
        raise HTTPException(
            status_code=422,
            detail="question (or a structured target) must not be empty",
        )
    session = ResearchSession(**fields)
    repository = get_repository(request)
    await repository.save_research_session(session)
    return session.model_dump(mode="json")


@router.get("/sessions")
async def list_research_sessions(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """All research sessions, newest first."""
    if status_filter is not None and status_filter not in __valid_statuses__:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of: {', '.join(sorted(__valid_statuses__))}",
        )
    repository = get_repository(request)
    sessions, total = await repository.list_research_sessions(
        status=status_filter, limit=limit, offset=offset
    )
    return {
        "items": [session.model_dump(mode="json") for session in sessions],
        **paginate(total, limit, offset),
    }


@router.get("/sessions/{research_session_id}")
async def get_research_session(
    research_session_id: UUID, request: Request
) -> dict[str, Any]:
    """One research session with its progress."""
    repository = get_repository(request)
    session = await repository.get_research_session(research_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="research session not found")
    return session.model_dump(mode="json")


@router.get("/sessions/{research_session_id}/runs")
async def get_research_session_runs(
    research_session_id: UUID, request: Request
) -> dict[str, Any]:
    """The dot investigations executed for a research session."""
    repository = get_repository(request)
    session = await repository.get_research_session(research_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="research session not found")
    runs = await repository.list_dot_runs_for_session(research_session_id)
    return {"items": [run.model_dump(mode="json") for run in runs], "total": len(runs)}


@router.get("/sessions/{research_session_id}/report")
async def get_research_session_report(
    research_session_id: UUID, request: Request
) -> dict[str, Any]:
    """The final synthesised research report of a completed session."""
    repository = get_repository(request)
    session = await repository.get_research_session(research_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="research session not found")
    if session.report_id is None:
        raise HTTPException(status_code=404, detail="session has no report yet")
    report = await repository.get_report(session.report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="report not found")
    return report.model_dump(mode="json")


@router.post("/sessions/{research_session_id}/cancel")
async def cancel_research_session(
    research_session_id: UUID,
    request: Request,
    _auth: None = Depends(require_command_token),
) -> dict[str, Any]:
    """Cancel a session and stop every unfinished dot run of it."""
    repository = get_repository(request)
    session = await repository.get_research_session(research_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="research session not found")
    if session.status in (
        ResearchSessionStatus.COMPLETED,
        ResearchSessionStatus.FAILED,
        ResearchSessionStatus.CANCELLED,
    ):
        raise HTTPException(
            status_code=409, detail=f"session is already {session.status.value}"
        )
    session.status = ResearchSessionStatus.CANCELLED
    session.finished_at = utcnow()
    await repository.save_research_session(session)
    stopped = await repository.cancel_dot_runs_for_session(research_session_id)
    return {
        "research_session_id": research_session_id,
        "status": ResearchSessionStatus.CANCELLED.value,
        "runs_cancelled": stopped,
    }
