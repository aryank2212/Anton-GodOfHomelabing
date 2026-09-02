"""Docker observer — container fleet state.

Lists every container and reports running / exited / unhealthy / restarting
as observations. Sentinel only reports; it never restarts containers (that is
Phoenix's job).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from app.config.loader import ObserverSpec
from app.config.settings import Settings
from app.core.clients import Clients
from app.core.logging import get_logger
from app.models.observation import Category, Observation, Severity
from app.observers.base import Observer

log = get_logger(__name__)


class DockerObserver(Observer):
    name = "docker"
    category = Category.INFRASTRUCTURE
    description = "Docker container fleet state"

    def __init__(self, spec: ObserverSpec, settings: Settings, clients: Clients) -> None:
        super().__init__(spec, default_interval=30.0, default_timeout=15.0)

    async def collect(self) -> Sequence[Observation]:
        return await asyncio.to_thread(self._collect_sync)

    def _collect_sync(self) -> list[Observation]:
        try:
            import docker
        except ImportError:  # pragma: no cover - docker is a dependency
            log.warning("docker_sdk_missing")
            return []

        try:
            client = docker.from_env(timeout=max(5, int(self.timeout)))
            containers = client.containers.list(all=True)
        except Exception as exc:  # noqa: BLE001 - daemon down / permissions
            log.warning("docker_unreachable", extra={"error": str(exc)})
            return [
                self._observation(
                    object="docker",
                    state="unavailable",
                    severity=Severity.MEDIUM,
                    confidence=0.8,
                    metadata={"error": str(exc)},
                    tags=["docker"],
                )
            ]

        observations = [
            self._observation(
                object="docker",
                state="ok",
                severity=Severity.INFO,
                confidence=0.95,
                metadata={"containers": len(containers)},
                tags=["docker"],
            )
        ]
        for container in containers:
            observations.append(self._container_observation(container))
        return observations

    def _container_observation(self, container: Any) -> Observation:
        attrs = container.attrs or {}
        state = attrs.get("State", {})
        health = state.get("Health", {}).get("Status")
        status = str(state.get("Status", container.status or "unknown"))
        restart_count = int(attrs.get("RestartCount", 0))
        image = ""
        image_meta = attrs.get("Config", {}).get("Image")
        if image_meta:
            image = str(image_meta)

        if status == "running":
            if health == "unhealthy":
                perceived, severity = "unhealthy", Severity.HIGH
            elif health == "starting":
                perceived, severity = "starting", Severity.LOW
            else:
                perceived, severity = "running", Severity.INFO
        elif status == "restarting":
            perceived, severity = "restarting", Severity.HIGH
        elif status == "exited":
            perceived, severity = "exited", Severity.MEDIUM
        else:
            perceived, severity = status or "unknown", Severity.MEDIUM

        return self._observation(
            object=f"container:{container.name}",
            state=perceived,
            severity=severity,
            confidence=0.95,
            metadata={
                "image": image,
                "status": status,
                "health": health,
                "restart_count": restart_count,
            },
            tags=["docker", "container"],
        )


def build_docker_observer(spec: ObserverSpec, settings: Settings, clients: Clients) -> Observer:
    return DockerObserver(spec, settings, clients)
