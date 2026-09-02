"""Environment sensor registry.

New sensor integrations register their class here. Sentinel ships without
any hardware sensors; the registry exists so extension modules can plug in.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.environment.base import EnvironmentSensor

log = get_logger(__name__)

Factory = Any


class SensorRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Factory] = {}

    def register(self, kind: str, factory: Factory) -> None:
        self._factories[kind] = factory

    def build(self, kind: str, params: dict[str, Any]) -> EnvironmentSensor:
        factory = self._factories.get(kind)
        if factory is None:
            raise ValueError(
                f"unknown environment sensor type '{kind}'; known: {sorted(self._factories)}"
            )
        return factory(params)


def empty_registry() -> SensorRegistry:
    """Registry with no sensors (Sentinel ships without hardware sensors)."""
    return SensorRegistry()
