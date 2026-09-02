"""SQLAlchemy ORM models backing Phoenix persistence.

``Incident`` is the persistent incident history. ``Maintenance`` is the
maintenance log used to suppress automated recovery during planned windows.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Incident(Base):
    __tablename__ = "incidents"

    incident_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    component: Mapped[str] = mapped_column(String(128), index=True)
    failure_type: Mapped[str] = mapped_column(String(128), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="error")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    detected_by: Mapped[str] = mapped_column(String(32), default="monitor")
    recovery_strategy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recovery_result: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Incident {self.incident_id} {self.component} {self.status}>"


class IncidentEvent(Base):
    """One step in the recovery timeline of an incident."""

    __tablename__ = "incident_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("incidents.incident_id", ondelete="CASCADE"), index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    stage: Mapped[str] = mapped_column(String(32))  # detect / recover / verify / close
    step: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text, default="")
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<IncidentEvent {self.incident_id} {self.stage} {self.step}>"


class Maintenance(Base):
    __tablename__ = "maintenance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    component: Mapped[str] = mapped_column(String(128), index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
