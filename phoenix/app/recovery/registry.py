"""Recovery registry — maps strategy names to strategy classes.

Built-in strategies: ``docker_restart``, ``systemd_restart``,
``http_retry``, ``noop``, ``dependency_restart``.

To add a strategy: subclass ``RecoveryStrategy`` and call ``registry.register``.
No other code changes are required.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.recovery.base import RecoveryStrategy

log = get_logger(__name__)

StrategyClass = type[RecoveryStrategy]


class RecoveryRegistry:
    def __init__(self) -> None:
        self._classes: dict[str, StrategyClass] = {}

    def register(self, name: str, strategy_class: StrategyClass) -> None:
        self._classes[name] = strategy_class

    def supports(self, name: str) -> bool:
        return name in self._classes

    def build(
        self, name: str, params: dict[str, Any], clients: Any, **kwargs: Any
    ) -> RecoveryStrategy:
        strategy_class = self._classes.get(name)
        if strategy_class is None:
            raise ValueError(
                f"unknown recovery strategy '{name}'; known strategies: {sorted(self._classes)}"
            )
        strategy = strategy_class(params=params, clients=clients, **kwargs)
        log.debug(
            "strategy_built",
            extra={"strategy": name},
        )
        return strategy


def default_registry() -> RecoveryRegistry:
    """Registry with all built-in recovery strategies."""
    from app.recovery.dependency import DependencyRestartStrategy
    from app.recovery.docker import DockerRestartStrategy
    from app.recovery.http import HTTPRetryStrategy
    from app.recovery.noop import NoopStrategy
    from app.recovery.systemd import SystemdRestartStrategy

    registry = RecoveryRegistry()
    registry.register("dependency_restart", DependencyRestartStrategy)
    registry.register("docker_restart", DockerRestartStrategy)
    registry.register("http_retry", HTTPRetryStrategy)
    registry.register("noop", NoopStrategy)
    registry.register("systemd_restart", SystemdRestartStrategy)
    return registry
