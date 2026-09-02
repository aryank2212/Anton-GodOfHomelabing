"""Environment — sensor extension points (no hardware sensors ship yet)."""

from app.environment.base import EnvironmentSensor, EnvironmentSensorError, EnvReading
from app.environment.registry import SensorRegistry, empty_registry

__all__ = [
    "EnvReading",
    "EnvironmentSensor",
    "EnvironmentSensorError",
    "SensorRegistry",
    "empty_registry",
]
