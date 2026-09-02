"""Device inventory and presence models.

Sentinel tracks every device it has ever seen on the network. Known devices
come from ``devices.yaml``; unknown devices are learned from observations.
The presence engine turns the current device set into a household status.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.observation import utcnow


class DeviceKind(StrEnum):
    PHONE = "phone"
    LAPTOP = "laptop"
    DESKTOP = "desktop"
    TABLET = "tablet"
    ROUTER = "router"
    SERVER = "server"
    SENSOR = "sensor"
    UNKNOWN = "unknown"


class Device(BaseModel):
    """A device known to be (or have been) on the network."""

    device_key: str = Field(min_length=1, max_length=128)
    mac: str | None = None
    name: str = ""
    known: bool = False
    owner: str | None = None
    category: DeviceKind = DeviceKind.UNKNOWN
    vendor: str | None = None
    ips: list[str] = Field(default_factory=list)
    hostnames: list[str] = Field(default_factory=list)
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    online: bool = False
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def display_name(self) -> str:
        return self.name or self.hostnames[0] if self.hostnames else (self.mac or self.device_key)

    def to_record_dict(self) -> dict[str, Any]:
        return {
            "device_key": self.device_key,
            "mac": self.mac,
            "name": self.name,
            "known": self.known,
            "owner": self.owner,
            "category": self.category.value,
            "vendor": self.vendor,
            "ips": self.ips,
            "hostnames": self.hostnames,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "online": self.online,
            "confidence": self.confidence,
            "metadata": self.metadata,
            "updated_at": self.updated_at,
        }


class DeviceEvent(BaseModel):
    """A lifecycle event in a device's history (joined / left / seen)."""

    device_key: str
    event: str = Field(pattern=r"^(joined|left|seen)$")
    timestamp: datetime = Field(default_factory=utcnow)
    mac: str | None = None
    ip: str | None = None
    hostname: str | None = None
    source: str = ""
    known: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class PresenceStatus(StrEnum):
    HOME_OCCUPIED = "home_occupied"
    NOBODY_HOME = "nobody_home"
    UNKNOWN_PRESENT = "unknown_present"
    MULTIPLE_USERS = "multiple_users"


# Human readable labels, used in /presence and in Hermes event titles.
PRESENCE_LABELS: dict[PresenceStatus, str] = {
    PresenceStatus.HOME_OCCUPIED: "Home Occupied",
    PresenceStatus.NOBODY_HOME: "Nobody Home",
    PresenceStatus.UNKNOWN_PRESENT: "Unknown Device Present",
    PresenceStatus.MULTIPLE_USERS: "Multiple Users Home",
}


class PresenceState(BaseModel):
    """The presence engine's current understanding of the household."""

    status: PresenceStatus
    label: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    people: list[str] = Field(default_factory=list)
    devices_online: list[str] = Field(default_factory=list)
    unknown_devices: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, _context: Any) -> None:
        self.label = PRESENCE_LABELS[self.status]

    def to_record_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "label": self.label,
            "confidence": self.confidence,
            "people": self.people,
            "devices_online": self.devices_online,
            "unknown_devices": self.unknown_devices,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


class PresenceChange(BaseModel):
    """A transition between two presence states."""

    previous: PresenceStatus | None
    current: PresenceState
    observation_id: UUID | None = None
