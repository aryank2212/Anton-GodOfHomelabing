"""SQLAlchemy ORM models backing Sentinel persistence.

All timestamps are stored as naive UTC (see ``app/database/session.py``);
the repository attaches the UTC timezone when building domain objects.

Tables:
* ``observations``      — immutable observation log
* ``situations``        — correlated situations (persistent while active)
* ``devices``           — device inventory (one row per device key)
* ``device_history``    — device lifecycle events (joined / left / seen)
* ``presence_history``  — presence status samples
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ObservationRecord(Base):
    __tablename__ = "observations"

    observation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    object_: Mapped[str] = mapped_column("object", String(128), index=True)
    state: Mapped[str] = mapped_column(String(64), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    tags_text: Mapped[str] = mapped_column(String(1024), default="", index=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Observation {self.observation_id} {self.source} {self.state}>"


class SituationRecord(Base):
    __tablename__ = "situations"

    situation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    summary: Mapped[str] = mapped_column(Text, default="")
    derived_from: Mapped[list] = mapped_column(JSON, default=list)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Situation {self.situation_id} {self.type} {self.status}>"


class DeviceRecord(Base):
    __tablename__ = "devices"

    device_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    mac: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    known: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str] = mapped_column(String(32), default="unknown")
    vendor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ips: Mapped[list] = mapped_column(JSON, default=list)
    hostnames: Mapped[list] = mapped_column(JSON, default=list)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    online: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Device {self.device_key} known={self.known} online={self.online}>"


class DeviceHistoryRecord(Base):
    __tablename__ = "device_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_key: Mapped[str] = mapped_column(String(128), index=True)
    event: Mapped[str] = mapped_column(String(16), index=True)  # joined / left / seen
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    mac: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="")
    known: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<DeviceHistory {self.device_key} {self.event}>"


class PresenceRecord(Base):
    __tablename__ = "presence_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    label: Mapped[str] = mapped_column(String(64), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    people: Mapped[list] = mapped_column(JSON, default=list)
    devices_online: Mapped[list] = mapped_column(JSON, default=list)
    unknown_devices: Mapped[list] = mapped_column(JSON, default=list)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Presence {self.status} {self.timestamp}>"
