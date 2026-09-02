from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class EventCreate(BaseModel):
    """Payload accepted by ``POST /event``.

    ``module`` and ``type`` follow a dotted or slash convention chosen by the
    emitting module (e.g. ``watcher.disk``). They are free-form strings that
    the rule engine matches against.
    """

    model_config = ConfigDict(extra="forbid")

    module: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=128)
    severity: Severity = Severity.INFO
    title: str = Field(min_length=1, max_length=512)
    message: str = Field(default="", max_length=16384)
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=64)
    correlation_id: UUID | None = None


class EventData(EventCreate):
    """A stored event, as returned by ``GET /events``."""

    id: UUID
    timestamp: datetime


class EventResponse(BaseModel):
    """Acknowledgment returned by ``POST /event``."""

    id: UUID
    status: str = "queued"


class Pagination(BaseModel):
    limit: int
    offset: int
    total: int
    next_offset: int | None = None


class EventList(BaseModel):
    items: list[EventData]
    pagination: Pagination
