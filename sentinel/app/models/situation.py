"""Situation model — a higher-level, correlated understanding.

A Situation answers "what is happening" by combining several observations
(e.g. ``Router Offline`` + ``UPS On Battery`` -> ``Power Outage``). Situations
are produced by the correlation engine and stay persistent while active.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.models.observation import Severity, utcnow


class SituationStatus(StrEnum):
    ACTIVE = "active"
    RESOLVED = "resolved"


class Situation(BaseModel):
    """A correlated situation derived from observations."""

    situation_id: UUID = Field(default_factory=uuid4)
    rule_id: str = Field(min_length=1, max_length=64)
    type: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    status: SituationStatus = SituationStatus.ACTIVE
    severity: Severity = Severity.INFO
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    summary: str = Field(default="", max_length=512)
    derived_from: list[UUID] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    resolved_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.status == SituationStatus.ACTIVE

    def resolve(self, *, at: datetime | None = None, summary: str | None = None) -> Situation:
        """Return a resolved copy of this situation."""
        now = at or utcnow()
        return self.model_copy(
            update={
                "status": SituationStatus.RESOLVED,
                "resolved_at": now,
                "updated_at": now,
                "summary": summary or self.summary,
            }
        )

    def to_record_dict(self) -> dict[str, Any]:
        """Flatten into the SQLAlchemy column layout."""
        return {
            "situation_id": str(self.situation_id),
            "rule_id": self.rule_id,
            "type": self.type,
            "name": self.name,
            "status": self.status.value,
            "severity": self.severity.value,
            "confidence": self.confidence,
            "summary": self.summary,
            "derived_from": [str(i) for i in self.derived_from],
            "sources": self.sources,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "resolved_at": self.resolved_at,
        }
