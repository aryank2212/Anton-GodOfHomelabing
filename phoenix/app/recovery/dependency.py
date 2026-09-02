"""Gracefully restart dependent services.

After a component recovers it is often necessary to bounce its consumers so
they re-establish connections to the dependency. This strategy restarts every
service that depends on the given component, using each dependent's own
configured recovery strategy, in a safe order.

The strategy needs the dependency graph and a resolver that maps a dependent
component name to its recovery strategy; the orchestrator provides both.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.logging import get_logger
from app.recovery.base import RecoveryError, RecoveryResult, RecoveryStrategy

log = get_logger(__name__)


class DependencyRestartStrategy(RecoveryStrategy):
    """Restart every component that depends on ``params["component"]``."""

    name = "dependency_restart"

    def __init__(
        self,
        params: dict[str, Any],
        clients: Any,
        graph: Any | None = None,
        resolve: Callable[[str], RecoveryStrategy] | None = None,
    ) -> None:
        super().__init__(params, clients)
        self.component = str(params["component"])
        self.graph = graph
        self.resolve = resolve or (lambda _name: NoopRecovery({}, None))

    async def execute(self) -> RecoveryResult:
        if self.graph is None:
            raise RecoveryError("dependency graph not provided to dependency_restart")
        dependents = self.graph.dependents_of(self.component)
        restarted: list[str] = []
        failures: list[str] = []
        for name in dependents:
            strategy = self.resolve(name)
            try:
                await strategy.execute()
                restarted.append(name)
                log.info(
                    "dependent_restarted",
                    extra={"dependency": self.component, "dependent": name},
                )
            except Exception as exc:  # noqa: BLE001 - report, keep going
                failures.append(f"{name}: {exc}")
                log.warning(
                    "dependent_restart_failed",
                    extra={"dependency": self.component, "dependent": name, "error": str(exc)},
                )
        if failures:
            raise RecoveryError(
                f"failed to restart dependents of '{self.component}': {'; '.join(failures)}"
            )
        return RecoveryResult.ok(
            self.name,
            f"restarted dependents of '{self.component}': {', '.join(restarted) or 'none'}",
            component=self.component,
            dependents=restarted,
        )


class NoopRecovery(RecoveryStrategy):
    """Fallback when no resolver is configured — do nothing successfully."""

    name = "noop"

    async def execute(self) -> RecoveryResult:
        return RecoveryResult.ok(self.name, "no-op")
