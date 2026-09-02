"""Monitor scheduler — the continuous Observe loop of Phoenix.

Every ``tick_interval`` seconds the scheduler runs every monitor whose
interval is due, up to ``max_concurrent_checks`` at a time. Failures are
handed to the orchestrator (recovery workflow); successes close stale open
incidents (self-healing).

Monitors of the same name never run concurrently.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from app.config.models import MonitorSpec, PhoenixConfig
from app.core.logging import get_logger
from app.models.check import MonitorResult
from app.monitors.base import Monitor
from app.services.snapshot import HealthSnapshot

log = get_logger(__name__)


class SchedulerOrchestrator(Protocol):
    """What the scheduler needs from the orchestrator."""

    async def handle_failure(self, monitor: Monitor, result: MonitorResult) -> None: ...

    async def handle_recovery(self, monitor: Monitor, result: MonitorResult) -> None: ...


@dataclass
class ScheduledMonitor:
    spec: MonitorSpec
    monitor: Monitor
    interval: float
    next_run: float = field(default_factory=time.monotonic)


class MonitorScheduler:
    def __init__(
        self,
        config: PhoenixConfig,
        monitors: list[Monitor],
        orchestrator: SchedulerOrchestrator,
        snapshot: HealthSnapshot,
        *,
        tick_interval: float,
        max_concurrent: int,
    ) -> None:
        self._specs: dict[str, MonitorSpec] = {m.name: m for m in config.monitors}
        self._scheduled = [
            ScheduledMonitor(
                spec=self._specs[monitor.name],
                monitor=monitor,
                interval=self._specs[monitor.name].interval,
            )
            for monitor in monitors
        ]
        self._orchestrator = orchestrator
        self._snapshot = snapshot
        self._tick_interval = tick_interval
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._running: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._stopped = False
        self.last_tick: datetime | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def monitor_count(self) -> int:
        return len(self._scheduled)

    def monitor_names(self) -> list[str]:
        return [sm.monitor.name for sm in self._scheduled]

    def spec_for(self, name: str) -> MonitorSpec | None:
        return self._specs.get(name)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopped = False
        self._task = asyncio.create_task(self._loop(), name="phoenix-scheduler")
        log.info(
            "scheduler_started",
            extra={
                "monitors": self.monitor_count,
                "tick_interval": self._tick_interval,
            },
        )

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        log.info("scheduler_stopped")

    async def run_once(self) -> None:
        """Run all due monitors now (used by tests and manual triggers)."""
        await self._run_due()

    async def _loop(self) -> None:
        while not self._stopped:
            self.last_tick = datetime.now(UTC)
            await self._run_due()
            await asyncio.sleep(self._tick_interval)

    async def _run_due(self) -> None:
        now = time.monotonic()
        due = [sm for sm in self._scheduled if now >= sm.next_run]
        if not due:
            return
        log.debug("scheduler_tick", extra={"due": len(due)})
        await asyncio.gather(*(self._run(sm) for sm in due), return_exceptions=True)
        for sm in due:
            sm.next_run = time.monotonic() + sm.interval

    async def _run(self, scheduled: ScheduledMonitor) -> None:
        name = scheduled.monitor.name
        if name in self._running:
            return
        self._running.add(name)
        try:
            async with self._semaphore:
                result = await scheduled.monitor.check()
        finally:
            self._running.discard(name)

        self._snapshot.record(name, result)
        if result.ok:
            log.debug("monitor_ok", extra={"monitor": name, "status": result.status})
            await self._orchestrator.handle_recovery(scheduled.monitor, result)
        else:
            log.warning(
                "monitor_failed",
                extra={
                    "monitor": name,
                    "status": result.status,
                    "detail": result.detail,
                },
            )
            await self._orchestrator.handle_failure(scheduled.monitor, result)
