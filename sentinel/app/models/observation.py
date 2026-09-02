"""Observation model — the atomic unit of Sentinel's perception.

Every piece of information collected by an Observer becomes one Observation.
Observations are immutable records: once created they are never modified.
Correlation and presence engines read them; nobody writes to them.

Sentinel never sends these records to users. Correlated situations are
published to Hermes as standardized events instead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    """Observational severity. Uses a 5-step scale so thresholds stay simple."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Category(StrEnum):
    """Top-level domains Sentinel can observe."""

    NETWORK = "network"
    PRESENCE = "presence"
    POWER = "power"
    ENVIRONMENT = "environment"
    INFRASTRUCTURE = "infrastructure"
    SECURITY = "security"
    SYSTEM = "system"


def utcnow() -> datetime:
    """Timezone-aware UTC now used across Sentinel."""
    return datetime.now(UTC)


class Observation(BaseModel):
    """An immutable, standardized record of something Sentinel perceived."""

    model_config = ConfigDict(frozen=True)

    observation_id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=utcnow)
    source: str = Field(min_length=1, max_length=64)
    category: Category
    severity: Severity = Severity.INFO
    object: str = Field(min_length=1, max_length=128, description="What is observed")
    state: str = Field(min_length=1, max_length=64, description="Perceived state")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)

    def with_tags(self, *tags: str) -> Observation:
        """Return a copy with the given tags added (Observations stay immutable)."""
        return self.model_copy(update={"tags": sorted(set([*self.tags, *tags]))})

    def to_record_dict(self) -> dict[str, Any]:
        """Flatten into the SQLAlchemy column layout."""
        return {
            "observation_id": str(self.observation_id),
            "timestamp": self.timestamp,
            "source": self.source,
            "category": self.category.value,
            "severity": self.severity.value,
            "object": self.object,
            "state": self.state,
            "confidence": self.confidence,
            "metadata": self.metadata,
            "tags": self.tags,
        }
