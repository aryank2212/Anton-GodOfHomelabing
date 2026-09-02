"""Intelligence report model — the output product of Argus.

A report distils confirmed hypotheses plus their supporting evidence and
entities into a structured, human- (and Oracle-) readable document.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.models.base import utcnow


class ReportStatus(StrEnum):
    DRAFT = "draft"
    FINAL = "final"


class Report(BaseModel):
    """A structured intelligence report."""

    report_id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=256)
    summary: str = Field(default="", max_length=8192)
    sections: list[dict[str, Any]] = Field(default_factory=list)
    hypothesis_ids: list[UUID] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)
    entity_ids: list[UUID] = Field(default_factory=list)
    status: ReportStatus = ReportStatus.DRAFT
    created_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_record_dict(self) -> dict[str, Any]:
        return {
            "report_id": str(self.report_id),
            "title": self.title,
            "summary": self.summary,
            "sections": self.sections,
            "hypothesis_ids": [str(value) for value in self.hypothesis_ids],
            "evidence_ids": [str(value) for value in self.evidence_ids],
            "entity_ids": [str(value) for value in self.entity_ids],
            "status": self.status.value,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }
