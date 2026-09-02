"""Docker Engine API gateway for Forge.

Wraps the ``docker`` SDK (same approach as Phoenix) with a deliberately small
control surface. Everything is synchronous under the hood and is executed in a
thread by the tools so the event loop never blocks.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.tools.base import ToolResult


class DockerUnavailableError(Exception):
    """Raised when the daemon cannot be reached or the SDK is missing."""


class DockerGateway:
    def __init__(self, timeout: float = 20.0) -> None:
        self._timeout = timeout
        self._client: Any = None

    def _api(self) -> Any:
        if self._client is None:
            try:
                import docker
            except ImportError as exc:  # pragma: no cover - depends on env
                raise DockerUnavailableError("docker SDK is not installed") from exc
            try:
                self._client = docker.from_env(timeout=self._timeout)
                self._client.ping()
            except Exception as exc:  # noqa: BLE001 - daemon down / perms
                raise DockerUnavailableError(f"docker daemon unreachable: {exc}") from exc
        return self._client

    @property
    def available(self) -> bool:
        try:
            self._api()
            return True
        except DockerUnavailableError:
            return False

    # -- reads -----------------------------------------------------------------

    def ps(self) -> list[dict[str, Any]]:
        api = self._api()
        rows: list[dict[str, Any]] = []
        for container in api.containers.list(all=True):
            state = container.attrs.get("State", {})
            health = state.get("Health", {})
            rows.append(
                {
                    "name": container.name,
                    "image": container.image.tags[0] if container.image.tags else None,
                    "status": str(state.get("Status") or container.status),
                    "running": bool(state.get("Running")),
                    "health": health.get("Status") if health else None,
                }
            )
        return sorted(rows, key=lambda r: r["name"])

    def inspect(self, target: str) -> dict[str, Any]:
        api = self._api()
        container = api.containers.get(target)
        return container.attrs

    def logs(self, target: str, tail: int = 100) -> str:
        api = self._api()
        container = api.containers.get(target)
        return (container.logs(tail=tail, timestamps=True) or b"").decode(errors="replace")

    def stats(self, target: str) -> dict[str, Any]:
        api = self._api()
        container = api.containers.get(target)
        raw = container.stats(stream=False)
        cpu_delta = float(raw["cpu_stats"]["cpu_usage"]["total_usage"]) - float(
            raw["precpu_stats"]["cpu_usage"]["total_usage"]
        )
        system_delta = float(raw["cpu_stats"]["system_cpu_usage"]) - float(
            raw["precpu_stats"]["system_cpu_usage"]
        )
        cpu_pct = round(cpu_delta / system_delta * 100.0, 2) if system_delta else 0.0
        mem = raw.get("memory_stats", {})
        mem_usage = int(mem.get("usage") or 0)
        mem_limit = int(mem.get("limit") or 0)
        mem_pct = round(mem_usage / mem_limit * 100.0, 2) if mem_limit else 0.0
        return {
            "cpu_percent": cpu_pct,
            "mem_usage_bytes": mem_usage,
            "mem_limit_bytes": mem_limit,
            "mem_percent": mem_pct,
        }

    # -- actions -----------------------------------------------------------------

    def restart(self, target: str) -> None:
        api = self._api()
        api.containers.get(target).restart(timeout=30)

    def start(self, target: str) -> None:
        api = self._api()
        api.containers.get(target).start()

    def stop(self, target: str) -> None:
        api = self._api()
        api.containers.get(target).stop(timeout=30)

    def current_image(self, target: str) -> str | None:
        api = self._api()
        container = api.containers.get(target)
        tags = container.image.tags
        return tags[0] if tags else None

    def rollback_to_image(self, target: str, image: str) -> None:
        """Recreate ``target`` from ``image`` preserving its config, then start
        it. The old container is renamed (not removed) so the rollback is
        reversible. This is deliberately destructive enough to be high-risk.
        """
        api = self._api()
        api.pull(image)
        inspect = api.inspect_container(target)
        config = inspect.get("Config") or {}
        host = inspect.get("HostConfig") or {}
        ts = int(time.time())
        api.rename(target, f"{target}.rollback-{ts}")
        try:
            api.create_container(
                image,
                name=target,
                command=config.get("Cmd"),
                entrypoint=config.get("Entrypoint"),
                hostname=config.get("Hostname"),
                user=config.get("User"),
                env=config.get("Env"),
                labels=config.get("Labels"),
                detach=True,
                ports=host.get("PortBindings"),
                volumes=host.get("Binds"),
                network_mode=host.get("NetworkMode"),
                restart_policy=host.get("RestartPolicy"),
            )
            api.start(target)
        except Exception:
            api.rename(f"{target}.rollback-{ts}", target)
            raise


async def run_in_thread(fn, *args: Any) -> Any:
    return await asyncio.to_thread(fn, *args)


def tool_result_error(error: str) -> ToolResult:
    return ToolResult(ok=False, output=error, error=error)
