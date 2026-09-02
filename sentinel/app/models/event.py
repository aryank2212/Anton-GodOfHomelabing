"""Standardized events published to Hermes.

Sentinel never contacts notification providers. It only posts these events to
Hermes' ``POST /event`` endpoint; Hermes decides whether and where to notify.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

MODULE = "sentinel"

# Hermes event ``type`` values emitted by Sentinel.
EVENT_PRESENCE_CHANGE = "presence_change"
EVENT_SITUATION_ACTIVATED = "situation_activated"
EVENT_SITUATION_RESOLVED = "situation_resolved"
EVENT_DEVICE_JOINED = "device_joined"
EVENT_DEVICE_LEFT = "device_left"
EVENT_DEVICE_UNKNOWN_JOINED = "device_unknown_joined"

# Hermes severity vocabulary (debug|info|warning|error|critical).
HERMES_SEVERITY: dict[str, str] = {
    "info": "info",
    "low": "info",
    "medium": "warning",
    "high": "error",
    "critical": "critical",
}


class HermesEvent(BaseModel):
    """Payload accepted by Hermes' ``POST /event``."""

    module: str = MODULE
    type: str = Field(min_length=1, max_length=128)
    severity: str = Field(default="info", pattern=r"^(debug|info|warning|error|critical)$")
    title: str = Field(min_length=1, max_length=512)
    message: str = Field(default="", max_length=16384)
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=64)
    correlation_id: UUID | None = Field(default_factory=uuid4)
