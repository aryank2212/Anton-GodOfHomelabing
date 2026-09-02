"""SQLAlchemy ORM models backing Argus persistence.

All timestamps are stored as naive UTC (see ``app/database/session.py``); the
repository attaches the UTC timezone when building domain objects.

Tables:
* ``evidence``          — immutable collected items (ContentItem)
* ``entities``          — resolved knowledge-graph entities
* ``entity_relations``  — directed edges between entities
* ``changes``           — discrete change facts
* ``hypotheses``        — testable claims backed by evidence
* ``reports``           — intelligence report documents
* ``dot_runs``          — on-demand dot-matching investigations
* ``dot_batches``       — individual search rounds of a dot run
* ``dot_watches``       — scheduled re-runs of dot topics
* ``research_sessions`` — goal-directed investigations composed of dot runs
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class EvidenceRecord(Base):
    __tablename__ = "evidence"

    content_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    source_type: Mapped[str] = mapped_column(String(16), index=True)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(512))
    body: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    tags: Mapped[list] = mapped_column(JSON, default=list)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Evidence {self.content_id} {self.source} {self.title!r}>"


class EntityRecord(Base):
    __tablename__ = "entities"

    entity_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(256))
    name_key: Mapped[str] = mapped_column(String(320), index=True, unique=True)
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    first_seen: Mapped[datetime] = mapped_column(DateTime)
    last_seen: Mapped[datetime] = mapped_column(DateTime, index=True)
    times_seen: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(64), default="")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Entity {self.kind}:{self.name}>"


class EntityRelationRecord(Base):
    __tablename__ = "entity_relations"

    relation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    subject_id: Mapped[str] = mapped_column(String(36), index=True)
    relation: Mapped[str] = mapped_column(String(64))
    object_id: Mapped[str] = mapped_column(String(36), index=True)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    first_seen: Mapped[datetime] = mapped_column(DateTime)
    last_seen: Mapped[datetime] = mapped_column(DateTime, index=True)
    times_seen: Mapped[int] = mapped_column(Integer, default=1)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Relation {self.subject_id} -{self.relation}-> {self.object_id}>"


class ChangeRecord(Base):
    __tablename__ = "changes"

    change_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    change_type: Mapped[str] = mapped_column(String(32), index=True)
    object_key: Mapped[str] = mapped_column(String(256), index=True)
    attribute: Mapped[str | None] = mapped_column(String(128), nullable=True)
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evidence_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    detected_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Change {self.change_type} {self.object_key}>"


class HypothesisRecord(Base):
    __tablename__ = "hypotheses"

    hypothesis_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    statement: Mapped[str] = mapped_column(String(1024))
    rationale: Mapped[str] = mapped_column(Text, default="")
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    entity_ids: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.3)
    status: Mapped[str] = mapped_column(String(16), index=True)
    oracle_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Hypothesis {self.status} {self.statement[:40]}>"


class ReportRecord(Base):
    __tablename__ = "reports"

    report_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    summary: Mapped[str] = mapped_column(Text, default="")
    sections: Mapped[list] = mapped_column(JSON, default=list)
    hypothesis_ids: Mapped[list] = mapped_column(JSON, default=list)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    entity_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Report {self.status} {self.title}>"


class DotRunRecord(Base):
    __tablename__ = "dot_runs"

    dot_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    topic: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), index=True)
    iterations_target: Mapped[int] = mapped_column(Integer, default=12)
    iterations_done: Mapped[int] = mapped_column(Integer, default=0)
    providers: Mapped[list] = mapped_column(JSON, default=list)
    queries_per_round: Mapped[int] = mapped_column(Integer, default=3)
    max_items_per_round: Mapped[int] = mapped_column(Integer, default=12)
    dots_kept: Mapped[int] = mapped_column(Integer, default=0)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text, default="")
    reasoning_log: Mapped[list] = mapped_column(JSON, default=list)
    report_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<DotRun {self.status} {self.topic[:40]}>"


class DotWatchRecord(Base):
    __tablename__ = "dot_watches"

    dot_watch_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    topic: Mapped[str] = mapped_column(String(512))
    iterations: Mapped[int] = mapped_column(Integer, default=12)
    providers: Mapped[list] = mapped_column(JSON, default=list)
    queries_per_round: Mapped[int] = mapped_column(Integer, default=3)
    max_items_per_round: Mapped[int] = mapped_column(Integer, default=12)
    interval_hours: Mapped[float] = mapped_column(Float, default=24.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_dot_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<DotWatch {'enabled' if self.enabled else 'disabled'} {self.topic[:40]}>"


class DotBatchRecord(Base):
    __tablename__ = "dot_batches"

    batch_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dot_run_id: Mapped[str] = mapped_column(String(36), index=True)
    iteration: Mapped[int] = mapped_column(Integer)
    queries: Mapped[list] = mapped_column(JSON, default=list)
    hits_found: Mapped[int] = mapped_column(Integer, default=0)
    content_ids: Mapped[list] = mapped_column(JSON, default=list)
    kept_ids: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<DotBatch #{self.iteration} run={self.dot_run_id[:8]}>"


class ResearchSessionRecord(Base):
    __tablename__ = "research_sessions"

    research_session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    context: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str] = mapped_column(String(16), default="single_pass")
    status: Mapped[str] = mapped_column(String(16), index=True)
    max_angles: Mapped[int] = mapped_column(Integer, default=3)
    angles_planned: Mapped[list] = mapped_column(JSON, default=list)
    runs_completed: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text, default="")
    report_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ResearchSession {self.status} {self.question[:40]}>"
