"""Test doubles for the external systems Phoenix drives.

Phoenix is designed around dependency injection: every monitor and recovery
strategy talks to a client interface, so tests inject fakes instead of real
Docker / systemd / HTTP targets.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from app.core.clients import Clients, DockerClient, SystemdClient


class FakeDockerClient(DockerClient):
    """Configurable stand-in for ``DockerClient``."""

    def __init__(
        self,
        *,
        exists: bool = True,
        running: bool = True,
        health_status: str | None = None,
    ) -> None:
        self.exists = exists
        self.running = running
        self.health_status = health_status
        self.restart_calls: list[str] = []

    def container_exists(self, name: str) -> bool:
        return self.exists

    def container_state(self, name: str) -> dict[str, Any]:
        if not self.exists:
            raise RuntimeError(f"container '{name}' not found")
        return {
            "running": self.running,
            "healthy": self.health_status == "healthy",
            "health_status": self.health_status,
            "status": "running" if self.running else "exited",
        }

    def restart_container(self, name: str, wait_seconds: float = 10.0) -> None:
        if not self.exists:
            raise RuntimeError(f"container '{name}' not found")
        self.restart_calls.append(name)
        self.running = True


class FakeSystemdClient(SystemdClient):
    """Configurable stand-in for ``SystemdClient``."""

    def __init__(self, *, available: bool = True, active: bool = True) -> None:
        self._available = available
        self.active = active
        self.restart_calls: list[str] = []

    @property
    def available(self) -> bool:
        return self._available

    async def unit_is_active(self, unit: str) -> bool:
        return self.active

    async def restart_unit(self, unit: str) -> None:
        self.restart_calls.append(unit)
        self.active = True


def mock_http_transport(*handlers: httpx.Response) -> httpx.MockTransport:
    """Serve the given responses in order, repeating the last one."""

    index = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal index
        i = min(index, len(handlers) - 1)
        index += 1
        return handlers[i]

    return httpx.MockTransport(handler)


class FakePublisher:
    """Records every event Phoenix publishes (replaces Hermes)."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> bool:
        self.events.append(event)
        return True


async def build_orchestrator(
    tmp_path: Path,
    config_yaml: str,
    *,
    docker: FakeDockerClient | None = None,
    systemd: FakeSystemdClient | None = None,
    http_responses: list[httpx.Response] | None = None,
):
    """Wire a full Phoenix stack with fake clients for workflow tests.

    Returns a ``(env, docker, systemd)`` tuple where ``env`` exposes the
    orchestrator and its collaborators.
    """
    from app.config.loader import load_config
    from app.database.repository import Repository
    from app.database.session import Database
    from app.monitors.registry import default_registry as default_monitor_registry
    from app.recovery.registry import default_registry as default_recovery_registry
    from app.services.dependency_graph import DependencyGraph
    from app.services.incidents import IncidentService
    from app.services.maintenance import MaintenanceService
    from app.services.orchestrator import Orchestrator
    from app.services.snapshot import HealthSnapshot

    config_file = tmp_path / "phoenix.yaml"
    config_file.write_text(config_yaml, encoding="utf-8")
    config = load_config(config_file)

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'phoenix.db'}")
    await database.init()
    repository = Repository(database.session_factory)
    incidents = IncidentService(repository)
    maintenance = MaintenanceService(repository)

    docker = docker or FakeDockerClient()
    systemd = systemd or FakeSystemdClient()
    if http_responses:
        http = httpx.AsyncClient(transport=mock_http_transport(*http_responses))
    else:
        http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))

    clients = Clients(docker=docker, systemd=systemd, http=http)
    publisher = FakePublisher()
    snapshot = HealthSnapshot()
    graph = DependencyGraph(config)
    orchestrator = Orchestrator(
        config=config,
        clients=clients,
        incidents=incidents,
        maintenance=maintenance,
        publisher=publisher,
        recovery_registry=default_recovery_registry(),
        monitor_registry=default_monitor_registry(),
        graph=graph,
    )

    class Env:
        def __init__(self) -> None:
            self.orchestrator = orchestrator
            self.incidents = incidents
            self.repository = repository
            self.publisher = publisher
            self.snapshot = snapshot
            self.config = config
            self.database = database
            self.clients = clients

        async def dispose(self) -> None:
            await http.aclose()
            await database.dispose()

    return Env(), docker, systemd


def mock_clients(
    *,
    docker: FakeDockerClient | None = None,
    systemd: FakeSystemdClient | None = None,
    http: httpx.AsyncClient | None = None,
) -> tuple[Clients, FakeDockerClient, FakeSystemdClient]:
    """Build a ``Clients`` bundle and return the fakes for asserts.

    Returns ``(clients, docker_fake, systemd_fake)``.
    """
    docker = docker or FakeDockerClient()
    systemd = systemd or FakeSystemdClient()
    http = http or httpx.AsyncClient(transport=mock_http_transport())
    return Clients(docker=docker, systemd=systemd, http=http), docker, systemd
