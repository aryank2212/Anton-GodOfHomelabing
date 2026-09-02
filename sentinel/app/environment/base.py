"""Environment awareness extension points.

No hardware sensors are implemented yet. This package only defines the
contract future temperature / humidity / air-quality / water-leak / smoke /
gas / door / window integrations must implement. Observers for those will be
built on top of ``EnvironmentSensor`` without touching core code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.observation import utcnow


class EnvReading(BaseModel):
    """One reading from an environment sensor."""

    metric: str  # temperature | humidity | air_quality | ...
    value: float
    unit: str = ""
    timestamp: datetime = Field(default_factory=utcnow)
    metadata: dict = Field(default_factory=dict)


class EnvironmentSensor(ABC):
    """Interface every environment data source implements."""

    name: str = "abstract"
    metric: str = "unknown"
    unit: str = ""

    @abstractmethod
    async def read(self) -> EnvReading | None:
        """Return the current reading, or None when unavailable."""

    async def close(self) -> None:  # pragma: no cover - optional hook
        return None


class EnvironmentSensorError(Exception):
    """Raised when a sensor cannot be read."""
