"""Configurable retry policy engine.

The YAML configuration describes a ``retry`` block per component::

    retry:
      attempts: 3        # number of attempts per recovery run
      backoff: 5         # seconds to wait before the second attempt
      multiplier: 2      # backoff grows by this factor between attempts
      max_backoff: 60    # backoff never exceeds this many seconds

This engine runs an arbitrary async callable with that policy. When every
attempt fails, ``RetryExhausted`` is raised and the caller decides how to
escalate (mark the incident unresolved and publish a critical event).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from app.core.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")

AttemptFn = Callable[[int], Awaitable[T]]


class RetryExhausted(Exception):
    """Raised when all configured attempts failed."""

    def __init__(self, attempts: int, last_error: Exception | None = None) -> None:
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(f"all {attempts} attempts failed")


@dataclass
class RetryPolicy:
    """A backoff schedule: attempt -> wait until the next attempt."""

    attempts: int = 3
    backoff: float = 5.0
    multiplier: float = 2.0
    max_backoff: float = 60.0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RetryPolicy:
        if not data:
            return cls()
        return cls(
            attempts=int(data.get("attempts", cls.attempts)),
            backoff=float(data.get("backoff", cls.backoff)),
            multiplier=float(data.get("multiplier", cls.multiplier)),
            max_backoff=float(data.get("max_backoff", cls.max_backoff)),
        )

    def delays(self) -> list[float]:
        """Wait in seconds to apply *after* each failed attempt."""
        delays: list[float] = []
        delay = self.backoff
        for _ in range(max(0, self.attempts - 1)):
            delays.append(delay)
            delay = min(delay * self.multiplier, self.max_backoff)
        return delays


async def run_with_retry(
    policy: RetryPolicy,
    fn: AttemptFn[T],
    *,
    component: str | None = None,
    strategy: str | None = None,
) -> T:
    """Run ``fn`` under ``policy``.

    ``fn`` receives the 1-based attempt number. On every failure we log the
    outcome and wait according to the backoff schedule before the next
    attempt. After the final failure ``RetryExhausted`` is raised.
    """
    delays = policy.delays()
    last_error: Exception | None = None
    for attempt in range(1, policy.attempts + 1):
        try:
            result = await fn(attempt)
            log.info(
                "retry_succeeded",
                extra={
                    "component": component,
                    "recovery_strategy": strategy,
                    "attempt": attempt,
                },
            )
            return result
        except Exception as exc:  # noqa: BLE001 - retries are for any failure
            last_error = exc
            log.warning(
                "retry_attempt_failed",
                extra={
                    "component": component,
                    "recovery_strategy": strategy,
                    "attempt": attempt,
                    "error": str(exc),
                },
            )
            if attempt < policy.attempts:
                await asyncio.sleep(delays[attempt - 1])
    raise RetryExhausted(policy.attempts, last_error) from last_error
