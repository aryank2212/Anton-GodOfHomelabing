"""Restart a Docker container."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.clients import ClientUnavailableError
from app.recovery.base import RecoveryError, RecoveryResult, RecoveryStrategy

if TYPE_CHECKING:
    from app.core.clients import DockerClient


class DockerRestartStrategy(RecoveryStrategy):
    """Restart a container through the Docker daemon.

    ``params``:
      container:     container name (required)
      wait_seconds:  restart timeout / settle time in seconds (default 10)
    """

    name = "docker_restart"

    def __init__(self, params: dict[str, Any], clients: Any) -> None:
        super().__init__(params, clients)
        self.container = str(params["container"])
        self.wait_seconds = float(params.get("wait_seconds", 10))

    async def execute(self) -> RecoveryResult:
        client: DockerClient = self.clients.docker
        if client is None:
            raise RecoveryError("no docker client available")
        try:
            client.restart_container(self.container, wait_seconds=self.wait_seconds)
        except ClientUnavailableError as exc:
            raise RecoveryError(f"failed to restart '{self.container}': {exc}") from exc
        return RecoveryResult.ok(
            self.name,
            f"container '{self.container}' restarted",
            container=self.container,
        )
