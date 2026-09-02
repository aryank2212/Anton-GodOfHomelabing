"""Recovery strategy interface.

A strategy is a pure action: it performs one recovery attempt on a client
(docker, systemd, HTTP) and reports the outcome. Strategies never retry —
the retry policy engine wraps them — and they never talk to Hermes or the
database; that is the orchestrator's job.

To add a strategy: subclass ``RecoveryStrategy``, implement ``execute`` and
register it in the recovery registry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class RecoveryError(Exception):
    """Raised when a recovery attempt fails; the retry engine catches it."""


class RecoveryResult(BaseModel):
    success: bool
    strategy: str
    message: str = ""
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    detail: dict[str, Any] = Field(default_factory=dict)

    @property
    def duration(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    @classmethod
    def ok(cls, strategy: str, message: str = "", **detail: Any) -> RecoveryResult:
        return cls(success=True, strategy=strategy, message=message, detail=detail)


class RecoveryStrategy(ABC):
    """One attempt at recovering a failed component."""

    name: str = "abstract"

    def __init__(self, params: dict[str, Any], clients: Any) -> None:
        self.params = params
        self.clients = clients

    @abstractmethod
    async def execute(self) -> RecoveryResult:
        """Perform one recovery attempt. Raise ``RecoveryError`` on failure."""
