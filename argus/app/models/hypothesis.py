"""Hypothesis model — the output of the hypothesis engine.

A hypothesis is a testable claim about the monitored world, backed by a set of
evidence and entities. It starts as ``proposed``, gains support as more
evidence arrives (``supported``), may be ``confirmed`` by enough evidence, or
``refuted`` / ``withdrawn`` when the picture changes. Confirmed hypotheses feed
intelligence reports.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.models.base import utcnow


class HypothesisStatus(StrEnum):
    PROPOSED = "proposed"
    SUPPORTED = "supported"
    CONFIRMED = "confirmed"
    REFUTED = "refuted"
    WITHDRAWN = "withdrawn"


class Hypothesis(BaseModel):
    """A testable claim grounded in Argus' evidence."""

    hypothesis_id: UUID = Field(default_factory=uuid4)
    statement: str = Field(min_length=1, max_length=1024)
    rationale: str = Field(default="", max_length=4096)
    evidence_ids: list[UUID] = Field(default_factory=list)
    entity_ids: list[UUID] = Field(default_factory=list)
    confidence: float = Field(default=0.3, ge=0.0, le=1.0)
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    oracle_generated: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def promote(self, status: HypothesisStatus) -> Hypothesis:
        """Return a copy with the status moved to the given state."""
        return self.model_copy(update={"status": status, "updated_at": utcnow()})

    def to_record_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": str(self.hypothesis_id),
            "statement": self.statement,
            "rationale": self.rationale,
            "evidence_ids": [str(value) for value in self.evidence_ids],
            "entity_ids": [str(value) for value in self.entity_ids],
            "confidence": self.confidence,
            "status": self.status.value,
            "oracle_generated": self.oracle_generated,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }
