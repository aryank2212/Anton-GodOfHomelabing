"""Monitor registry — maps monitor ``type`` names to implementations.

Built-in types: ``docker``, ``systemd``, ``http``, ``disk``, ``memory``,
``cpu``, ``network``.

To add a monitor type: subclass ``Monitor``, then call ``registry.register``
with the factory or subclass ``MonitorRegistry``. No other code changes.
"""

from __future__ import annotations

from typing import Any

from app.config.models import MonitorSpec
from app.core.clients import Clients
from app.core.logging import get_logger
from app.monitors.base import Monitor

log = get_logger(__name__)

Factory = Any  # Callable[[str, dict[str, Any], Clients], Monitor]


class MonitorRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Factory] = {}

    def register(self, kind: str, factory: Factory) -> None:
        self._factories[kind] = factory

    def supports(self, kind: str) -> bool:
        return kind in self._factories

    def build(self, spec: MonitorSpec, clients: Clients) -> Monitor:
        factory = self._factories.get(spec.type)
        if factory is None:
            raise ValueError(
                f"monitor '{spec.name}' uses unknown type '{spec.type}'; "
                f"known types: {sorted(self._factories)}"
            )
        monitor = factory(spec.name, spec.params, clients)
        log.debug(
            "monitor_built",
            extra={"monitor": monitor.name, "kind": monitor.kind},
        )
        return monitor


def default_registry() -> MonitorRegistry:
    """Registry with all built-in monitor types."""
    from app.monitors.cpu import build_cpu_monitor
    from app.monitors.disk import build_disk_monitor
    from app.monitors.docker import build_docker_monitor
    from app.monitors.http import build_http_monitor
    from app.monitors.memory import build_memory_monitor
    from app.monitors.network import build_network_monitor
    from app.monitors.systemd import build_systemd_monitor

    registry = MonitorRegistry()
    registry.register("cpu", build_cpu_monitor)
    registry.register("disk", build_disk_monitor)
    registry.register("docker", build_docker_monitor)
    registry.register("http", build_http_monitor)
    registry.register("memory", build_memory_monitor)
    registry.register("network", build_network_monitor)
    registry.register("systemd", build_systemd_monitor)
    return registry
