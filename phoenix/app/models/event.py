"""Standardized event payload published to Hermes.

Phoenix never contacts notification providers directly. It posts these events
to Hermes' ``POST /event`` endpoint and Hermes decides what to do next.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

MODULE = "phoenix"

# Hermes event ``type`` values emitted by Phoenix.
EVENT_RECOVERY_SUCCESS = "recovery_success"
EVENT_RECOVERY_FAILED = "recovery_failed"
EVENT_RECOVERY_ESCALATED = "recovery_escalated"
EVENT_INCIDENT_OPENED = "incident_opened"


class HermesEvent(BaseModel):
    """Payload accepted by Hermes' ``POST /event``."""

    module: str = MODULE
    type: str = Field(min_length=1)
    severity: str = Field(default="warning", pattern=r"^(debug|info|warning|error|critical)$")
    title: str = Field(min_length=1, max_length=512)
    message: str = Field(default="", max_length=16384)
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=64)
    correlation_id: str | None = None
