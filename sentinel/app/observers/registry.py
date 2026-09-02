"""Observer registry — maps observer names to implementations.

Built-in observers: ``system``, ``docker``, ``router``, ``watcher``, ``ups``,
``http``, ``network``.

To add an observer type: subclass ``Observer`` and register a factory (see
``default_registry``). No other code changes are needed.
"""

from __future__ import annotations

from collections.abc import Callable

from app.config.loader import ObserversConfig, ObserverSpec
from app.config.settings import Settings
from app.core.clients import Clients
from app.core.logging import get_logger
from app.observers.base import Observer

log = get_logger(__name__)

Factory = Callable[[ObserverSpec, Settings, Clients], Observer]


class ObserverRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Factory] = {}

    def register(self, name: str, factory: Factory) -> None:
        self._factories[name] = factory

    def supports(self, name: str) -> bool:
        return name in self._factories

    def build_all(
        self,
        config: ObserversConfig,
        settings: Settings,
        clients: Clients,
    ) -> list[Observer]:
        """Build every enabled observer, respecting the global env filter."""
        observers: list[Observer] = []
        for name, spec in config.observers.items():
            if name not in settings.enabled_observer_names:
                continue
            factory = self._factories.get(name)
            if factory is None:
                log.warning("observer_unknown", extra={"observer": name})
                continue
            try:
                observer = factory(spec, settings, clients)
            except Exception as exc:  # noqa: BLE001 - never kill startup
                log.error("observer_build_failed", extra={"observer": name, "error": str(exc)})
                continue
            if not observer.enabled:
                log.debug("observer_disabled", extra={"observer": name})
                continue
            observers.append(observer)
        return observers


def default_registry() -> ObserverRegistry:
    """Registry with all built-in observer types."""
    from app.observers.docker import build_docker_observer
    from app.observers.http import build_http_observer
    from app.observers.network import build_network_observer
    from app.observers.router import build_router_observer
    from app.observers.system import build_system_observer
    from app.observers.ups import build_ups_observer
    from app.observers.watcher import build_watcher_observer

    registry = ObserverRegistry()
    registry.register("system", build_system_observer)
    registry.register("docker", build_docker_observer)
    registry.register("router", build_router_observer)
    registry.register("watcher", build_watcher_observer)
    registry.register("ups", build_ups_observer)
    registry.register("http", build_http_observer)
    registry.register("network", build_network_observer)
    return registry
