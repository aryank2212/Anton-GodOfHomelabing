"""Collector scheduler — the ingestion loop.

Each collector runs in its own asyncio task. Every cycle the collector's
``collect`` is awaited (bounded by its timeout) and the resulting items are
handed to an ingest callback. Failures never kill the loop: they are recorded
on the status object and surfaced via ``/sources`` and ``/health``.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from app.core.logging import get_logger
from app.models.base import utcnow
from app.models.content import ContentItem
from app.models.event import HermesEvent
from app.sources.base import Collector

log = get_logger(__name__)

IngestCallback = Callable[[Sequence[ContentItem]], Awaitable[None]]

EVENT_COLLECTOR_DEGRADED = "collector_degraded"
EVENT_COLLECTOR_RECOVERED = "collector_recovered"


class Publisher(Protocol):
    """Minimal event publisher used for degrade/recover notifications."""

    async def publish(self, event: HermesEvent) -> bool: ...


@dataclass
class CollectorStatus:
    """Live health of one collector's collect loop."""

    name: str
    enabled: bool
    interval: float
    timeout: float
    running: bool = False
    last_collect_at: datetime | None = None
    last_error: str | None = None
    items_count: int = 0
    consecutive_failures: int = 0
    degraded: bool = False
    backoff: float = 0.0
    next_run_at: datetime | None = None
    last_success_at: datetime | None = None
    details: dict = field(default_factory=dict)


class CollectorScheduler:
    def __init__(
        self,
        collectors: list[Collector],
        *,
        default_timeout: float = 30.0,
        jitter: float = 2.0,
        backoff_base: float = 60.0,
        backoff_max: float = 3600.0,
        failure_threshold: int = 3,
        publisher: Publisher | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.collectors = collectors
        self.default_timeout = default_timeout
        self.jitter = jitter
        self.backoff_base = max(0.0, backoff_base)
        self.backoff_max = max(0.0, backoff_max)
        self.failure_threshold = max(1, failure_threshold)
        self.publisher = publisher
        self._sleep = sleep or asyncio.sleep
        self._tasks: list[asyncio.Task[None]] = []
        self._status: dict[str, CollectorStatus] = {}
        self.running = False
        self.started_at: datetime | None = None
        self._ingest: IngestCallback | None = None

    def start(self, ingest: IngestCallback) -> None:
        if self.running:
            return
        self.running = True
        self.started_at = utcnow()
        self._ingest = ingest
        for collector in self.collectors:
            self._status[collector.name] = CollectorStatus(
                name=collector.name,
                enabled=collector.enabled,
                interval=collector.interval,
                timeout=collector.timeout if collector.timeout else self.default_timeout,
            )
        for collector in self.collectors:
            self._tasks.append(asyncio.create_task(self._run(collector)))
        log.info("scheduler_started", extra={"collectors": len(self.collectors)})

    async def stop(self) -> None:
        self.running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        for collector in self.collectors:
            try:
                await collector.shutdown()
            except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                log.warning(
                    "collector_shutdown_failed",
                    extra={"collector": collector.name, "error": str(exc)},
                )

    def statuses(self) -> list[CollectorStatus]:
        return list(self._status.values())

    def status(self, name: str) -> CollectorStatus | None:
        return self._status.get(name)

    async def _run(self, collector: Collector) -> None:
        status = self._status[collector.name]
        try:
            await collector.setup()
        except Exception as exc:  # noqa: BLE001 - setup failure must not crash
            status.last_error = f"setup: {exc}"
            log.error(
                "collector_setup_failed", extra={"collector": collector.name, "error": str(exc)}
            )
            return

        status.running = True
        try:
            while self.running:
                started = time.monotonic()
                failed = False
                try:
                    items = await asyncio.wait_for(
                        collector.collect(), timeout=collector.timeout or self.default_timeout
                    )
                except TimeoutError:
                    failed = True
                    status.last_error = "collect timed out"
                    log.warning(
                        "collector_timeout",
                        extra={"collector": collector.name, "timeout": collector.timeout},
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - keep the loop alive
                    failed = True
                    status.last_error = f"{type(exc).__name__}: {exc}"
                    log.error(
                        "collector_collect_failed",
                        extra={"collector": collector.name, "error": str(exc)},
                    )

                if failed:
                    status.consecutive_failures += 1
                    status.backoff = min(
                        self.backoff_max,
                        self.backoff_base * (2 ** (status.consecutive_failures - 1)),
                    )
                    if (
                        status.consecutive_failures >= self.failure_threshold
                        and not status.degraded
                    ):
                        status.degraded = True
                        log.warning(
                            "collector_degraded",
                            extra={
                                "collector": collector.name,
                                "consecutive_failures": status.consecutive_failures,
                            },
                        )
                        await self._notify(
                            EVENT_COLLECTOR_DEGRADED,
                            "warning",
                            f"Collector '{collector.name}' degraded",
                            (
                                f"{collector.name} has failed {status.consecutive_failures} "
                                f"times in a row ({status.last_error}). Backing off to "
                                f"{status.backoff:.0f}s."
                            ),
                            collector,
                        )
                else:
                    status.last_collect_at = utcnow()
                    status.last_error = None
                    status.items_count += len(items)
                    status.next_run_at = None
                    status.backoff = 0.0
                    was_degraded = status.degraded
                    if status.consecutive_failures > 0:
                        status.consecutive_failures = 0
                    status.last_success_at = utcnow()
                    status.degraded = False
                    if was_degraded:
                        log.info(
                            "collector_recovered",
                            extra={"collector": collector.name},
                        )
                        await self._notify(
                            EVENT_COLLECTOR_RECOVERED,
                            "info",
                            f"Collector '{collector.name}' recovered",
                            f"{collector.name} is collecting successfully again.",
                            collector,
                        )
                    if items and self._ingest is not None:
                        await self._ingest(items)

                elapsed = time.monotonic() - started
                interval = status.backoff if status.backoff else collector.interval
                status.next_run_at = utcnow() + timedelta(
                    seconds=max(0.1, interval - elapsed)
                )
                await self._sleep(
                    max(0.1, interval - elapsed + random.uniform(0, self.jitter))
                )
        finally:
            status.running = False

    async def _notify(
        self,
        event_type: str,
        severity: str,
        title: str,
        message: str,
        collector: Collector,
    ) -> None:
        if self.publisher is None or not getattr(self.publisher, "enabled", True):
            return
        try:
            await self.publisher.publish(
                HermesEvent(
                    type=event_type,
                    severity=severity,
                    title=title,
                    message=message,
                    metadata={
                        "collector": collector.name,
                        "status": self._status[collector.name].__dict__,
                    },
                    tags=["argus", "collector"],
                )
            )
        except Exception as exc:  # noqa: BLE001 - notify must never crash the loop
            log.warning(
                "collector_notify_failed",
                extra={"collector": collector.name, "event": event_type, "error": str(exc)},
            )
