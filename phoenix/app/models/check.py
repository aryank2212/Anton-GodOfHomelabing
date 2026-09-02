"""The outcome of a single health check."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class MonitorResult(BaseModel):
    """Result of running one monitor at one point in time."""

    ok: bool
    status: str = "unknown"  # e.g. "running", "healthy", "down", "full"
    detail: str = ""
    metric: dict[str, Any] = Field(default_factory=dict)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def healthy(cls, status: str = "ok", **metric: Any) -> MonitorResult:
        return cls(ok=True, status=status, metric=metric)

    @classmethod
    def failing(cls, status: str, detail: str, **metric: Any) -> MonitorResult:
        return cls(ok=False, status=status, detail=detail, metric=metric)
