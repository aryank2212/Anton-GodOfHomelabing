"""Research session model — a goal-directed investigation, end to end.

A dot run investigates one topic. A *research session* is the wider picture:
one question the operator wants answered, decomposed into a handful of research
angles (each executed as a dot run) and closed with a final synthesis report.

Phases after this one teach the session how to run: the three research modes
(single-pass / progressive / contradictory), the adaptive loop and the
information-gain stopping rule. The surface below already carries the ``mode``
field so those phases do not need to migrate the schema again.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.models.base import utcnow


class ResearchTarget(BaseModel):
    """A structured research target: the who/what, where, when and keywords.

    Optional on a session request. When provided it is validated at the API
    boundary and persisted as structured JSON under ``metadata["target"]`` so
    later phases (and the web UI) can action it without re-parsing prose.
    """

    target: str = Field(default="", max_length=500,
        description="The who/what to research (person, company, CVE, project…).")
    place: str = Field(default="", max_length=200,
        description="Geographic or organisational scope.")
    date_from: str = Field(default="", max_length=32,
        description="Start of the window of interest (free text or ISO date).")
    date_to: str = Field(default="", max_length=32,
        description="End of the window of interest.")
    keywords: list[str] = Field(default_factory=list,
        description="Additional terms to scope the search.")
    note: str = Field(default="", max_length=2000,
        description="Anything else the fields do not capture.")

    def has_any(self) -> bool:
        return bool(
            self.target.strip()
            or self.place.strip()
            or self.date_from.strip()
            or self.date_to.strip()
            or self.keywords
            or self.note.strip()
        )

    def as_question(self) -> str:
        """Compose the structured fields into a goal-directed research question."""
        parts: list[str] = []
        if self.target.strip():
            parts.append(f"the target is {self.target.strip()}")
        if self.place.strip():
            parts.append(f"located in/relating to {self.place.strip()}")
        if self.date_from.strip() and self.date_to.strip():
            parts.append(f"within the window {self.date_from.strip()} to {self.date_to.strip()}")
        elif self.date_from.strip():
            parts.append(f"since {self.date_from.strip()}")
        elif self.date_to.strip():
            parts.append(f"up to {self.date_to.strip()}")
        if self.keywords:
            words = ", ".join(k.strip() for k in self.keywords if k.strip())
            parts.append(f"relevant keywords: {words}")
        if self.note.strip():
            parts.append(self.note.strip())
        return "; ".join(parts) if parts else ""


class ResearchSessionMode(StrEnum):
    SINGLE_PASS = "single_pass"
    PROGRESSIVE = "progressive"
    CONTRADICTORY = "contradictory"


class ResearchSessionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResearchSession(BaseModel):
    """One goal-directed research question and its progress."""

    research_session_id: UUID = Field(default_factory=uuid4)
    question: str = Field(min_length=1, max_length=2000)
    context: str = Field(default="", max_length=4000)
    mode: ResearchSessionMode = ResearchSessionMode.SINGLE_PASS
    status: ResearchSessionStatus = ResearchSessionStatus.QUEUED
    max_angles: int = Field(default=3, ge=1, le=6)
    angles_planned: list[str] = Field(default_factory=list)
    runs_completed: int = Field(default=0, ge=0)
    summary: str = Field(default="", max_length=16384)
    report_id: UUID | None = None
    error: str | None = Field(default=None, max_length=4096)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_record_dict(self) -> dict[str, Any]:
        return {
            "research_session_id": str(self.research_session_id),
            "question": self.question,
            "context": self.context,
            "mode": self.mode.value,
            "status": self.status.value,
            "max_angles": self.max_angles,
            "angles_planned": self.angles_planned,
            "runs_completed": self.runs_completed,
            "summary": self.summary,
            "report_id": str(self.report_id) if self.report_id else None,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "metadata": self.metadata,
        }
