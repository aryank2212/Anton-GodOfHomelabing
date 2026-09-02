from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.event import EventData


def _uuid() -> str:
    return str(uuid4())


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EventRecord(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    module: Mapped[str] = mapped_column(String(128), index=True)
    type: Mapped[str] = mapped_column(String(128), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String(512))
    message: Mapped[str] = mapped_column(Text, default="")
    # "metadata" is the physical column name; "metadata_json" is the Python
    # attribute (the name "metadata" is reserved on declarative models).
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    # Delivery lifecycle: pending -> processing -> done.
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # One of: ignored | logged | notified | partial | skipped | failed.
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    notifications: Mapped[list[NotificationRecord]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    remediations: Mapped[list[RemediationRecord]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )

    def to_event_data(self) -> EventData:
        return EventData(
            id=self.id,
            timestamp=self.timestamp,
            module=self.module,
            type=self.type,
            severity=self.severity,
            title=self.title,
            message=self.message,
            metadata=self.metadata_json or {},
            tags=self.tags or [],
            correlation_id=self.correlation_id,
        )


class NotificationRecord(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_event_status", "event_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(64))
    # pending -> sent | failed.
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    event: Mapped[EventRecord] = relationship(back_populates="notifications")


class RemediationRecord(Base):
    __tablename__ = "remediations"
    __table_args__ = (Index("ix_remediations_event", "event_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    rule: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(512), default="")
    # pending -> done | failed.
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    event: Mapped[EventRecord] = relationship(back_populates="remediations")


class ChatMessage(Base):
    """One turn of an AI conversation, persisted per Telegram chat.

    Backs the Oracle client so follow-up questions keep their context across
    restarts instead of living only in memory.
    """

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_chat_created", "chat_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    # One of: user | assistant.
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )


def event_to_dict(event: EventRecord) -> dict[str, Any]:
    """Plain-dict projection of an event, used by the rule engine and renderer."""
    return {
        "id": event.id,
        "timestamp": event.timestamp.isoformat(),
        "module": event.module,
        "type": event.type,
        "severity": event.severity,
        "title": event.title,
        "message": event.message,
        "metadata": event.metadata_json or {},
        "tags": event.tags or [],
        "correlation_id": event.correlation_id,
    }
