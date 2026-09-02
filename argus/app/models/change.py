"""Change model — what the change detector reports.

A change is a discrete, dated fact about the monitored world: a tracked page
changed, an entity appeared for the first time, an attribute of an entity was
updated, or a new relation was established.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.models.base import utcnow
from app.models.content import Severity


class ChangeType(StrEnum):
    CONTENT_CHANGED = "content_changed"
    ATTRIBUTE_CHANGED = "attribute_changed"
    NEW_ENTITY = "new_entity"
    NEW_RELATION = "new_relation"
    ENTITY_SEEN = "entity_seen"


class Change(BaseModel):
    """A discrete fact the intelligence layer noticed."""

    change_id: UUID = Field(default_factory=uuid4)
    change_type: ChangeType
    object_key: str = Field(min_length=1, max_length=256)
    attribute: str | None = Field(default=None, max_length=128)
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    evidence_id: UUID | None = None
    severity: Severity = Severity.INFO
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    detected_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_record_dict(self) -> dict[str, Any]:
        return {
            "change_id": str(self.change_id),
            "change_type": self.change_type.value,
            "object_key": self.object_key,
            "attribute": self.attribute,
            "before": self.before,
            "after": self.after,
            "evidence_id": str(self.evidence_id) if self.evidence_id else None,
            "severity": self.severity.value,
            "confidence": self.confidence,
            "detected_at": self.detected_at,
            "metadata": self.metadata,
        }
