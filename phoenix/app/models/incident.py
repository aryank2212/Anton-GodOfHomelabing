"""Incident model — every failure becomes an Incident."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class IncidentStatus(str, Enum):
    OPEN = "open"
    RECOVERING = "recovering"
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    MAINTENANCE = "maintenance"


class IncidentCreate(BaseModel):
    """Data known at the moment a failure is detected."""

    component: str = Field(min_length=1)
    failure_type: str = Field(min_length=1)
    severity: str = Field(default="error", pattern=r"^(debug|info|warning|error|critical)$")
    detected_by: str = Field(default="monitor")  # monitor | manual | dependency
    metadata: dict[str, Any] = Field(default_factory=dict)


class Incident(IncidentCreate):
    """A stored incident, as returned by the API."""

    incident_id: str = Field(default_factory=lambda: uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: IncidentStatus = IncidentStatus.OPEN
    recovery_strategy: str | None = None
    recovery_result: bool | None = None
    duration: float | None = None  # seconds from detection to resolution
    attempts: int = 0
    correlation_id: str | None = None  # passed to Hermes as the event correlation id


class IncidentUpdate(BaseModel):
    """Fields that change during the incident lifecycle."""

    status: IncidentStatus | None = None
    recovery_strategy: str | None = None
    recovery_result: bool | None = None
    duration: float | None = None
    attempts: int | None = None
    correlation_id: str | None = None


class IncidentList(BaseModel):
    items: list[Incident]
    total: int
    limit: int
    offset: int
