"""Dot-run models — an on-demand internet investigation.

A "dot run" is a focused, iterative web investigation requested on demand:
given a topic, Argus searches the internet in batches and repeatedly asks
Oracle which of the freshly scraped items are relevant *dots* (dynamic pieces
of the story), keeps the strong matches, explores deeper from them each round,
and finally writes a reasoning log plus an intelligence report that explains
how and why each connection was considered relevant.

Inspired by the Infer planning pipeline (goal analysis -> planning -> worker
agents -> verification), adapted so the only brain — Oracle — lives on the
laptop, not on the server. The server only searches, fetches and stores.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.models.base import utcnow


class DotRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DotRun(BaseModel):
    """One dot-matching investigation."""

    dot_run_id: UUID = Field(default_factory=uuid4)
    topic: str = Field(min_length=1, max_length=512)
    status: DotRunStatus = DotRunStatus.QUEUED
    iterations_target: int = Field(default=12, ge=1, le=30)
    iterations_done: int = Field(default=0, ge=0)
    providers: list[str] = Field(
        default_factory=lambda: ["duckduckgo", "hackernews", "github"]
    )
    queries_per_round: int = Field(default=3, ge=1, le=8)
    max_items_per_round: int = Field(default=12, ge=1, le=50)
    dots_kept: int = Field(default=0, ge=0)
    evidence_count: int = Field(default=0, ge=0)
    summary: str = Field(default="", max_length=8192)
    reasoning_log: list[dict[str, Any]] = Field(default_factory=list)
    report_id: UUID | None = None
    session_id: UUID | None = None
    error: str | None = Field(default=None, max_length=4096)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_record_dict(self) -> dict[str, Any]:
        return {
            "dot_run_id": str(self.dot_run_id),
            "topic": self.topic,
            "status": self.status.value,
            "iterations_target": self.iterations_target,
            "iterations_done": self.iterations_done,
            "providers": self.providers,
            "queries_per_round": self.queries_per_round,
            "max_items_per_round": self.max_items_per_round,
            "dots_kept": self.dots_kept,
            "evidence_count": self.evidence_count,
            "summary": self.summary,
            "reasoning_log": self.reasoning_log,
            "report_id": str(self.report_id) if self.report_id else None,
            "session_id": str(self.session_id) if self.session_id else None,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }


class DotWatch(BaseModel):
    """A scheduled re-run of a dot investigation topic.

    The watch tick enqueues a fresh dot run whenever ``next_run_at`` is due,
    then reschedules; when a watch-driven run completes its dots are diffed
    against the previous watch run and the delta is published to Hermes.
    """

    dot_watch_id: UUID = Field(default_factory=uuid4)
    topic: str = Field(min_length=1, max_length=512)
    iterations: int = Field(default=12, ge=1, le=30)
    providers: list[str] = Field(
        default_factory=lambda: ["duckduckgo", "hackernews", "github"]
    )
    queries_per_round: int = Field(default=3, ge=1, le=8)
    max_items_per_round: int = Field(default=12, ge=1, le=50)
    interval_hours: float = Field(default=24.0, ge=0.1, le=8760)
    enabled: bool = Field(default=True)
    next_run_at: datetime = Field(default_factory=utcnow)
    last_run_id: UUID | None = None
    last_run_at: datetime | None = None
    last_dot_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def to_record_dict(self) -> dict[str, Any]:
        return {
            "dot_watch_id": str(self.dot_watch_id),
            "topic": self.topic,
            "iterations": self.iterations,
            "providers": self.providers,
            "queries_per_round": self.queries_per_round,
            "max_items_per_round": self.max_items_per_round,
            "interval_hours": self.interval_hours,
            "enabled": self.enabled,
            "next_run_at": self.next_run_at,
            "last_run_id": str(self.last_run_id) if self.last_run_id else None,
            "last_run_at": self.last_run_at,
            "last_dot_ids": self.last_dot_ids,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class DotBatch(BaseModel):
    """One search-and-match round of a dot run."""

    batch_id: UUID = Field(default_factory=uuid4)
    dot_run_id: UUID
    iteration: int = Field(ge=1)
    queries: list[str] = Field(default_factory=list)
    hits_found: int = Field(default=0, ge=0)
    content_ids: list[UUID] = Field(default_factory=list)
    kept_ids: list[UUID] = Field(default_factory=list)
    note: str = Field(default="", max_length=2048)
    created_at: datetime = Field(default_factory=utcnow)

    def to_record_dict(self) -> dict[str, Any]:
        return {
            "batch_id": str(self.batch_id),
            "dot_run_id": str(self.dot_run_id),
            "iteration": self.iteration,
            "queries": self.queries,
            "hits_found": self.hits_found,
            "content_ids": [str(value) for value in self.content_ids],
            "kept_ids": [str(value) for value in self.kept_ids],
            "note": self.note,
            "created_at": self.created_at,
        }
