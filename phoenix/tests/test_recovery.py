from __future__ import annotations

from app.models.event import EVENT_RECOVERY_ESCALATED, EVENT_RECOVERY_SUCCESS
from app.models.incident import IncidentStatus
from app.monitors.docker import DockerMonitor

from .helpers import FakeDockerClient, build_orchestrator

DOCKER_COMPONENT_CONFIG = """\
version: 1
monitors:
  - name: db_container
    type: docker
    interval: 30
    enabled: true
    severity: error
    params:
      container: postgres
components:
  - name: postgres
    monitors: [db_container]
    recovery:
      strategy: docker_restart
      params:
        container: postgres
      retry:
        attempts: 3
        backoff: 0.01
        multiplier: 2
        max_backoff: 0.1
      escalate:
        severity: critical
"""


def _failing_result():
    from app.models.check import MonitorResult

    return MonitorResult.failing("stopped", "container 'postgres' is exited")


def _ok_result():
    from app.models.check import MonitorResult

    return MonitorResult.healthy("running", container="postgres")


async def test_failure_detection_recovers_and_publishes(tmp_path) -> None:
    docker = FakeDockerClient(running=False)
    env, docker, _ = await build_orchestrator(tmp_path, DOCKER_COMPONENT_CONFIG, docker=docker)
    monitor = DockerMonitor("db_container", {"container": "postgres"}, docker)

    await env.orchestrator.handle_failure(monitor, _failing_result())

    incident = (await env.incidents.search(limit=10, offset=0))[0][0]
    assert incident.status == IncidentStatus.RESOLVED
    assert incident.recovery_strategy == "docker_restart"
    assert incident.recovery_result is True
    assert incident.duration is not None
    assert docker.restart_calls == ["postgres"]

    assert len(env.publisher.events) == 1
    event = env.publisher.events[0]
    assert event.type == EVENT_RECOVERY_SUCCESS
    assert event.correlation_id == incident.incident_id
    assert event.metadata["component"] == "postgres"
    assert event.tags == ["phoenix", "recovery", "success", "postgres"]
    await env.dispose()


async def test_dedup_skips_repeat_failures_while_open(tmp_path) -> None:
    class AlwaysFailingDocker(FakeDockerClient):
        def restart_container(self, name: str, wait_seconds: float = 10.0) -> None:
            raise RuntimeError("cannot restart")

    docker = AlwaysFailingDocker()
    env, docker, _ = await build_orchestrator(tmp_path, DOCKER_COMPONENT_CONFIG, docker=docker)
    monitor = DockerMonitor("db_container", {"container": "postgres"}, docker)

    # The scheduler would report the same failure on every tick.
    await env.orchestrator.handle_failure(monitor, _failing_result())
    await env.orchestrator.handle_failure(monitor, _failing_result())
    await env.orchestrator.handle_failure(monitor, _failing_result())

    incidents, _ = await env.incidents.search(limit=10, offset=0)
    assert len(incidents) == 1  # repeated reports collapsed while unresolved
    assert incidents[0].status == IncidentStatus.UNRESOLVED
    await env.dispose()


async def test_recovery_retries_then_succeeds(tmp_path) -> None:
    class FlakyDocker(FakeDockerClient):
        def __init__(self) -> None:
            super().__init__(running=False)
            self.failures_left = 2

        def restart_container(self, name: str, wait_seconds: float = 10.0) -> None:
            if self.failures_left > 0:
                self.failures_left -= 1
                raise RuntimeError("daemon busy")
            super().restart_container(name)

    docker = FlakyDocker()
    env, docker, _ = await build_orchestrator(tmp_path, DOCKER_COMPONENT_CONFIG, docker=docker)
    monitor = DockerMonitor("db_container", {"container": "postgres"}, docker)

    await env.orchestrator.handle_failure(monitor, _failing_result())

    incident = (await env.incidents.search(limit=10, offset=0))[0][0]
    assert incident.status == IncidentStatus.RESOLVED
    assert incident.attempts == 3
    assert len(env.publisher.events) == 1
    assert env.publisher.events[0].type == EVENT_RECOVERY_SUCCESS
    await env.dispose()


async def test_recovery_exhaustion_escalates(tmp_path) -> None:
    class AlwaysFailingDocker(FakeDockerClient):
        def restart_container(self, name: str, wait_seconds: float = 10.0) -> None:
            raise RuntimeError("cannot restart")

    docker = AlwaysFailingDocker()
    env, docker, _ = await build_orchestrator(tmp_path, DOCKER_COMPONENT_CONFIG, docker=docker)
    monitor = DockerMonitor("db_container", {"container": "postgres"}, docker)

    await env.orchestrator.handle_failure(monitor, _failing_result())

    incident = (await env.incidents.search(limit=10, offset=0))[0][0]
    assert incident.status == IncidentStatus.UNRESOLVED
    assert incident.recovery_result is False
    assert incident.attempts == 3

    event = env.publisher.events[0]
    assert event.type == EVENT_RECOVERY_ESCALATED
    assert event.severity == "critical"
    await env.dispose()


REPORT_ONLY_CONFIG = """\
version: 1
monitors:
  - name: disk_root
    type: disk
    interval: 60
    enabled: true
    severity: warning
    params:
      path: /
      threshold_pct: 90
components:
  - name: host
    monitors: [disk_root]
"""


async def test_report_only_component_stays_unresolved_then_auto_recovers(tmp_path) -> None:
    env, _, _ = await build_orchestrator(tmp_path, REPORT_ONLY_CONFIG)
    from app.monitors.disk import DiskMonitor

    monitor = DiskMonitor("disk_root", {"path": "/", "threshold_pct": 90})
    result = _ok_result()
    result.ok = False
    result.status = "full"
    result.detail = "/ is full"

    await env.orchestrator.handle_failure(monitor, result)

    incident = (await env.incidents.search(limit=10, offset=0))[0][0]
    assert incident.status == IncidentStatus.UNRESOLVED
    assert incident.recovery_strategy is None
    assert env.publisher.events[0].type == EVENT_RECOVERY_ESCALATED

    # Disk frees up -> monitor passes -> Phoenix closes the incident itself.
    await env.orchestrator.handle_recovery(monitor, _ok_result())
    incident = (await env.incidents.search(limit=10, offset=0))[0][0]
    assert incident.status == IncidentStatus.RESOLVED
    assert incident.recovery_strategy == "auto"
    await env.dispose()


async def test_maintenance_window_suppresses_recovery(tmp_path) -> None:
    docker = FakeDockerClient(running=False)
    env, docker, _ = await build_orchestrator(tmp_path, DOCKER_COMPONENT_CONFIG, docker=docker)
    monitor = DockerMonitor("db_container", {"container": "postgres"}, docker)

    from app.services.maintenance import MaintenanceCreate

    await env.orchestrator.maintenance.create(
        MaintenanceCreate(component="postgres", reason="upgrade")
    )

    await env.orchestrator.handle_failure(monitor, _failing_result())

    incident = (await env.incidents.search(limit=10, offset=0))[0][0]
    assert incident.status == IncidentStatus.MAINTENANCE
    assert docker.restart_calls == []
    assert env.publisher.events == []
    await env.dispose()


async def test_manual_recovery_of_component(tmp_path) -> None:
    docker = FakeDockerClient(running=False)
    env, docker, _ = await build_orchestrator(tmp_path, DOCKER_COMPONENT_CONFIG, docker=docker)

    incident = await env.orchestrator.recover_component("postgres")

    assert incident is not None
    assert incident.detected_by == "manual"
    assert incident.status == IncidentStatus.RESOLVED
    assert docker.restart_calls == ["postgres"]
    await env.dispose()


async def test_manual_recovery_unknown_component_returns_none(tmp_path) -> None:
    env, _, _ = await build_orchestrator(tmp_path, DOCKER_COMPONENT_CONFIG)
    assert await env.orchestrator.recover_component("nope") is None
    await env.dispose()


DEPENDENCY_CONFIG = """\
version: 1
monitors:
  - name: db_container
    type: docker
    interval: 30
    enabled: true
    severity: error
    params:
      container: postgres
  - name: paperless_container
    type: docker
    interval: 30
    enabled: true
    severity: warning
    params:
      container: paperless
components:
  - name: postgres
    monitors: [db_container]
    recovery:
      strategy: docker_restart
      params: {container: postgres}
      retry: {attempts: 1}
  - name: paperless
    monitors: [paperless_container]
    depends_on: [postgres]
    recovery:
      strategy: docker_restart
      params: {container: paperless}
      retry: {attempts: 1}
"""


async def test_dependency_cascade_restarts_dependents(tmp_path) -> None:
    docker = FakeDockerClient(running=False)
    env, docker, _ = await build_orchestrator(tmp_path, DEPENDENCY_CONFIG, docker=docker)
    monitor = DockerMonitor("db_container", {"container": "postgres"}, docker)

    await env.orchestrator.handle_failure(monitor, _failing_result())

    incidents, _ = await env.incidents.search(limit=10, offset=0)
    by_component = {inc.component: inc for inc in incidents}
    assert set(by_component) == {"postgres", "paperless"}
    assert by_component["postgres"].status == IncidentStatus.RESOLVED
    assert by_component["postgres"].detected_by == "monitor"
    assert by_component["paperless"].status == IncidentStatus.RESOLVED
    assert by_component["paperless"].detected_by == "dependency"
    assert by_component["paperless"].failure_type == "dependency_recovery"
    assert set(docker.restart_calls) == {"postgres", "paperless"}
    assert docker.restart_calls[0] == "postgres"

    success_types = [e.type for e in env.publisher.events]
    assert success_types == [EVENT_RECOVERY_SUCCESS, EVENT_RECOVERY_SUCCESS]
    await env.dispose()


async def test_hermes_event_payload_matches_hermes_schema(tmp_path) -> None:
    """Events published to Hermes satisfy Hermes' ``EventCreate`` contract."""
    from app.models.event import HermesEvent

    payload = HermesEvent(
        type="recovery_success",
        severity="warning",
        title="Jellyfin Restarted",
        message="Container successfully recovered.",
        metadata={"incident_id": "abc", "strategy": "docker_restart"},
        tags=["phoenix", "recovery"],
        correlation_id="abc",
    ).model_dump()

    # The fields Hermes requires are present with compatible types.
    assert payload["module"] == "phoenix"
    assert payload["type"]
    assert payload["severity"] in {"debug", "info", "warning", "error", "critical"}
    assert payload["title"]
    assert isinstance(payload["metadata"], dict)
    assert isinstance(payload["tags"], list)
    assert payload["correlation_id"]
