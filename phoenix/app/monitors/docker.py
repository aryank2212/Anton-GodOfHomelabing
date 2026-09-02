"""Docker container monitor."""

from __future__ import annotations

from typing import Any

from app.core.clients import ClientUnavailableError, DockerClient
from app.models.check import MonitorResult
from app.monitors.base import Monitor, safe_check


class DockerMonitor(Monitor):
    """Checks that a container is present and in the expected state.

    ``params``:
      container:        container name (required)
      expected_status:  ``running`` (default) or ``healthy``. With ``healthy``
                        a container without a healthcheck is accepted when it
                        is running.
    """

    kind = "docker"

    def __init__(self, name: str, params: dict[str, Any], client: DockerClient) -> None:
        super().__init__(name, params)
        self._client = client
        self.container = str(params["container"])
        self.expected_status = str(params.get("expected_status", "running"))

    @safe_check
    async def check(self) -> MonitorResult:
        state = self._client.container_state(self.container)
        if not state["running"]:
            return MonitorResult.failing(
                "stopped",
                f"container '{self.container}' is {state['status']}",
                container=self.container,
                container_status=state["status"],
            )
        if self.expected_status == "healthy":
            health = state.get("health_status")
            if health is not None and health != "healthy":
                return MonitorResult.failing(
                    "unhealthy",
                    f"container '{self.container}' health is {health}",
                    container=self.container,
                    health=health,
                )
        return MonitorResult.healthy(
            "running",
            container=self.container,
            container_status=state["status"],
            health=state.get("health_status"),
        )


def build_docker_monitor(name: str, params: dict[str, Any], clients: Any) -> Monitor:
    if clients.docker is None:
        raise ClientUnavailableError("no docker client available")
    return DockerMonitor(name, params, clients.docker)
