"""YAML configuration loading for Sentinel.

All the *description* of Anton lives in YAML: observer tuning, correlation
rules, known devices and MAC vendors. Loaders here parse and validate the
files so the rest of the code only ever sees typed objects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from app.core.logging import get_logger

log = get_logger(__name__)


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load a YAML file, returning an empty mapping when it is missing."""
    file = Path(path)
    if not file.exists():
        log.warning("config_missing", extra={"path": str(file)})
        return {}
    with file.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


# ---------------------------------------------------------------------------
# Observers
# ---------------------------------------------------------------------------


class HttpTarget(BaseModel):
    """A URL Sentinel periodically checks with the HTTP observer."""

    name: str = Field(min_length=1, max_length=64)
    url: str
    expected_status: int = 200
    timeout: float = 5.0


class ObserverSpec(BaseModel):
    """Tuning for a single observer (the dict key is its type/name)."""

    enabled: bool = True
    interval: float | None = None
    timeout: float | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    targets: list[HttpTarget] = Field(default_factory=list)


class ObserversConfig(BaseModel):
    version: int = 1
    observers: dict[str, ObserverSpec] = Field(default_factory=dict)


def load_observers_config(path: str | Path) -> ObserversConfig:
    raw = load_yaml(path)
    return ObserversConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# Known devices
# ---------------------------------------------------------------------------


class DeviceDefinition(BaseModel):
    """A known device declared by the administrator in ``devices.yaml``."""

    name: str = Field(min_length=1, max_length=128)
    mac: str | None = None
    ip: str | None = None
    hostname: str | None = None
    owner: str | None = None
    category: str = "unknown"
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class DevicesFile(BaseModel):
    version: int = 1
    devices: list[DeviceDefinition] = Field(default_factory=list)


def load_devices(path: str | Path) -> DevicesFile:
    raw = load_yaml(path)
    return DevicesFile.model_validate(raw)


# ---------------------------------------------------------------------------
# MAC vendors
# ---------------------------------------------------------------------------


class VendorsFile(BaseModel):
    version: int = 1
    vendors: dict[str, str] = Field(default_factory=dict)


def load_vendors(path: str | Path) -> dict[str, str]:
    raw = load_yaml(path)
    file = VendorsFile.model_validate(raw)
    return {prefix.lower().replace("-", ":"): vendor for prefix, vendor in file.vendors.items()}
