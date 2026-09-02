from __future__ import annotations

from app.config.models import ComponentSpec, MonitorSpec, PhoenixConfig
from app.core.scheduler import MonitorScheduler
from app.models.check import MonitorResult
from app.monitors.base import Monitor
from app.services.snapshot import HealthSnapshot


class StubMonitor(Monitor):
    kind = "stub"

    def __init__(self, name: str, *, ok: bool) -> None:
        super().__init__(name, {})
        self.ok = ok
        self.calls = 0

    async def check(self) -> MonitorResult:
        self.calls += 1
        if self.ok:
            return MonitorResult.healthy("ok")
        return MonitorResult.failing("down", "stub failure")


class StubOrchestrator:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.recoveries: list[str] = []

    async def handle_failure(self, monitor: Monitor, result: MonitorResult) -> None:
        self.failures.append(monitor.name)

    async def handle_recovery(self, monitor: Monitor, result: MonitorResult) -> None:
        self.recoveries.append(monitor.name)


def _config_with(monitor: StubMonitor) -> PhoenixConfig:
    return PhoenixConfig(
        monitors=[
            # type is validated by the config model; the scheduler does not
            # use the monitor registry, so any valid kind works here.
            MonitorSpec(name=monitor.name, type="http", interval=60, params={})
        ],
        components=[ComponentSpec(name="web", monitors=[monitor.name])],
    )


async def test_scheduler_runs_due_monitor_and_routes_failure() -> None:
    monitor = StubMonitor("web", ok=False)
    orchestrator = StubOrchestrator()
    snapshot = HealthSnapshot()
    scheduler = MonitorScheduler(
        _config_with(monitor),
        [monitor],
        orchestrator,
        snapshot,
        tick_interval=0.01,
        max_concurrent=4,
    )

    await scheduler.run_once()

    assert monitor.calls == 1
    assert orchestrator.failures == ["web"]
    assert orchestrator.recoveries == []
    result = snapshot.result("web")
    assert result is not None
    assert result.ok is False


async def test_scheduler_routes_healthy_result_to_recovery() -> None:
    monitor = StubMonitor("web", ok=True)
    orchestrator = StubOrchestrator()
    scheduler = MonitorScheduler(
        _config_with(monitor),
        [monitor],
        orchestrator,
        HealthSnapshot(),
        tick_interval=0.01,
        max_concurrent=4,
    )

    await scheduler.run_once()

    assert orchestrator.recoveries == ["web"]
    assert orchestrator.failures == []
