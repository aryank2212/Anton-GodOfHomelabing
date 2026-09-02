"""Thin clients for the external systems Phoenix drives.

Phoenix talks to two orchestrators on Anton:

* the Docker daemon (via the official ``docker`` SDK), and
* systemd (via the ``systemctl`` command line).

Both are wrapped behind a small interface so monitors and recovery strategies
depend on the abstraction, and tests can inject fakes.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class Clients:
    """External systems injected into monitors and recovery strategies."""

    docker: DockerClient | None = None
    systemd: SystemdClient | None = None
    http: httpx.AsyncClient | None = None

    @classmethod
    def defaults(cls, http_timeout: float = 5.0) -> Clients:
        return cls(
            docker=default_docker_client(),
            systemd=SystemdClient(),
            http=httpx.AsyncClient(
                timeout=http_timeout,
                follow_redirects=True,
                headers={"User-Agent": "anton-phoenix/1.0"},
            ),
        )

    async def aclose(self) -> None:
        if self.http is not None:
            await self.http.aclose()


class ClientUnavailableError(Exception):
    """Raised when an external system cannot be reached at all."""


class DockerClient:
    """Minimal Docker control surface.

    Uses the ``docker`` SDK which honours ``DOCKER_HOST`` and friends. The
    daemon socket is expected to be available (``/var/run/docker.sock``).
    """

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout
        self._client: Any = None

    def _api(self) -> Any:
        if self._client is None:
            try:
                import docker
            except ImportError as exc:  # pragma: no cover - depends on env
                raise ClientUnavailableError("docker SDK is not installed") from exc
            try:
                self._client = docker.from_env(timeout=self._timeout)
                self._client.ping()
            except Exception as exc:  # noqa: BLE001 - daemon down / perms
                raise ClientUnavailableError(f"docker daemon unreachable: {exc}") from exc
        return self._client

    def container_exists(self, name: str) -> bool:
        try:
            self._api().containers.get(name)
            return True
        except Exception:
            return False

    def container_state(self, name: str) -> dict[str, Any]:
        """Return ``{"running": bool, "healthy": bool | None, "status": str}``."""
        api = self._api()
        try:
            container = api.containers.get(name)
        except Exception as exc:  # noqa: BLE001
            raise ClientUnavailableError(f"container '{name}' not found: {exc}") from exc

        container.reload()
        state = container.attrs.get("State", {})
        running = bool(state.get("Running"))
        status = str(state.get("Status", "unknown"))
        health = state.get("Health", {})
        health_status = health.get("Status") if health else None
        return {
            "running": running,
            "healthy": health_status == "healthy",
            "health_status": health_status,
            "status": status,
        }

    def restart_container(self, name: str, wait_seconds: float = 10.0) -> None:
        api = self._api()
        container = api.containers.get(name)
        container.restart(timeout=min(int(wait_seconds), 300))
        log.info(
            "docker_restart_issued",
            extra={"container": name, "wait_seconds": wait_seconds},
        )


class SystemdClient:
    """Minimal systemd control surface via the ``systemctl`` CLI.

    When Phoenix runs inside a container this requires the host systemd bus
    to be reachable (mount ``/run/dbus/system_bus_socket``) and sufficient
    privileges (see the docker-compose file).
    """

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout
        self._executable = shutil.which("systemctl")

    @property
    def available(self) -> bool:
        return self._executable is not None

    async def _run(self, *args: str) -> tuple[int, str]:
        if self._executable is None:
            raise ClientUnavailableError("systemctl is not available on this host")
        cmd = [self._executable, *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        return proc.returncode or 0, stdout.decode(errors="replace").strip()

    async def unit_is_active(self, unit: str) -> bool:
        code, _ = await self._run("is-active", unit)
        return code == 0

    async def restart_unit(self, unit: str) -> None:
        code, output = await self._run("restart", unit)
        if code != 0:
            raise ClientUnavailableError(f"systemctl restart {unit} failed ({code}): {output}")
        log.info("systemd_restart_issued", extra={"unit": unit})


def default_docker_client() -> DockerClient | None:
    """Create a Docker client only when a daemon is likely reachable."""
    if os.path.exists("/var/run/docker.sock") or os.environ.get("DOCKER_HOST"):
        return DockerClient()
    return None
