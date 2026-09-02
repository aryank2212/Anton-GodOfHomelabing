"""Power awareness extension points.

Sentinel never shuts anything down — Phoenix does recovery. This package only
defines how power information is read and how it becomes standardized
observations. Hardware integration is done through ``PowerMonitor``
implementations (a NUT ``upsc``-based one ships in :mod:`app.power.ups`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.observation import utcnow


class PowerStatus(StrEnum):
    ON_LINE = "on_line"
    ON_BATTERY = "on_battery"
    LOW_BATTERY = "low_battery"
    UNKNOWN = "unknown"


class PowerSample(BaseModel):
    """A point-in-time snapshot of the power subsystem."""

    status: PowerStatus
    timestamp: datetime = Field(default_factory=utcnow)
    charge_percent: float | None = None
    runtime_seconds: float | None = None
    input_voltage: float | None = None
    battery_voltage: float | None = None
    metadata: dict = Field(default_factory=dict)


class PowerMonitor(ABC):
    """Interface every power data source implements."""

    name: str = "abstract"

    @abstractmethod
    async def read(self) -> PowerSample:
        """Read the current power state. Raises ``PowerMonitorError`` on failure."""

    async def close(self) -> None:  # pragma: no cover - optional hook
        return None


class PowerMonitorError(Exception):
    """Raised when the power source cannot be read."""
