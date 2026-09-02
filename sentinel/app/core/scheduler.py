"""Observer scheduler — the collection loop.

Each observer runs in its own asyncio task. Every cycle the observer's
``collect`` is awaited (bounded by its timeout) and the resulting observations
are handed to an ingest callback. Failures never kill the loop: they are
recorded on the status object and surfaced via ``/observers`` and ``/health``.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime

from app.core.logging import get_logger
from app.models.observation import Observation, utcnow
from app.observers.base import Observer

log = get_logger(__name__)

IngestCallback = Callable[[Sequence[Observation]], Awaitable[None]]


@dataclass
class ObserverStatus:
    """Live health of one observer's collect loop."""

    name: str
    enabled: bool
    interval: float
    timeout: float
    running: bool = False
    last_collect_at: datetime | None = None
    last_error: str | None = None
    observations_count: int = 0
    details: dict = field(default_factory=dict)


class ObserverScheduler:
    def __init__(
        self,
        observers: list[Observer],
        *,
        default_timeout: float = 10.0,
        jitter: float = 1.0,
    ) -> None:
        self.observers = observers
        self.default_timeout = default_timeout
        self.jitter = jitter
        self._tasks: list[asyncio.Task[None]] = []
        self._status: dict[str, ObserverStatus] = {}
        self.running = False
        self.started_at: datetime | None = None
        self._ingest: IngestCallback | None = None

    def start(self, ingest: IngestCallback) -> None:
        if self.running:
            return
        self.running = True
        self.started_at = utcnow()
        self._ingest = ingest
        for observer in self.observers:
            self._status[observer.name] = ObserverStatus(
                name=observer.name,
                enabled=observer.enabled,
                interval=observer.interval,
                timeout=observer.timeout if observer.timeout else self.default_timeout,
            )
        for observer in self.observers:
            self._tasks.append(asyncio.create_task(self._run(observer)))
        log.info("scheduler_started", extra={"observers": len(self.observers)})

    async def stop(self) -> None:
        self.running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        for observer in self.observers:
            try:
                await observer.shutdown()
            except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                log.warning(
                    "observer_shutdown_failed", extra={"observer": observer.name, "error": str(exc)}
                )

    def statuses(self) -> list[ObserverStatus]:
        return list(self._status.values())

    def status(self, name: str) -> ObserverStatus | None:
        return self._status.get(name)

    async def _run(self, observer: Observer) -> None:
        status = self._status[observer.name]
        try:
            await observer.setup()
        except Exception as exc:  # noqa: BLE001 - setup failure must not crash
            status.last_error = f"setup: {exc}"
            log.error("observer_setup_failed", extra={"observer": observer.name, "error": str(exc)})
            return

        status.running = True
        try:
            while self.running:
                started = time.monotonic()
                try:
                    observations = await asyncio.wait_for(
                        observer.collect(), timeout=observer.timeout or self.default_timeout
                    )
                    status.observations_count += len(observations)
                    status.last_collect_at = utcnow()
                    status.last_error = None
                    if observations and self._ingest is not None:
                        await self._ingest(observations)
                except TimeoutError:
                    status.last_error = "collect timed out"
                    log.warning(
                        "observer_timeout",
                        extra={"observer": observer.name, "timeout": observer.timeout},
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - keep the loop alive
                    status.last_error = f"{type(exc).__name__}: {exc}"
                    log.error(
                        "observer_collect_failed",
                        extra={"observer": observer.name, "error": str(exc)},
                    )
                elapsed = time.monotonic() - started
                await asyncio.sleep(
                    max(0.1, observer.interval - elapsed + random.uniform(0, self.jitter))
                )
        finally:
            status.running = False
