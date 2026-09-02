"""Standardized events published to Hermes.

Argus never contacts notification providers. It only posts these events to
Hermes' ``POST /event`` endpoint; Hermes decides whether and where to notify.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

MODULE = "argus"

# Hermes event ``type`` values emitted by Argus.
EVENT_HYPOTHESIS_CONFIRMED = "hypothesis_confirmed"
EVENT_REPORT_PUBLISHED = "report_published"
EVENT_CHANGE_DETECTED = "change_detected"
EVENT_ALERT = "intelligence_alert"
EVENT_DOTS_WATCH_UPDATE = "dots_watch_update"

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
