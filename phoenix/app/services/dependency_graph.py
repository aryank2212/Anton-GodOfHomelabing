"""Configurable dependency graph of Anton's components.

Derived entirely from each component's ``depends_on`` list. Phoenix uses it
to:

* restart dependent services after a dependency recovers (graceful cascade),
* avoid restarting unrelated services, and
* detect misconfigured (cyclic) graphs at startup.

Example (from the default config)::

    paperless  depends on  postgres  depends on  docker
"""

from __future__ import annotations

from app.config.models import ComponentSpec, PhoenixConfig
from app.core.logging import get_logger

log = get_logger(__name__)


class DependencyGraph:
    def __init__(self, config: PhoenixConfig) -> None:
        self._dependencies: dict[str, list[str]] = {}
        self._dependents: dict[str, list[str]] = {}
        for component in config.components:
            self._dependencies[component.name] = list(component.depends_on)
            for dependency in component.depends_on:
                self._dependents.setdefault(dependency, []).append(component.name)

    def dependencies_of(self, name: str) -> list[str]:
        """Components that ``name`` directly depends on."""
        return list(self._dependencies.get(name, []))

    def dependents_of(self, name: str) -> list[str]:
        """Components that directly depend on ``name``."""
        return list(self._dependents.get(name, []))

    def has_cycles(self) -> list[list[str]]:
        """Return all cycles found in the graph (empty when acyclic)."""
        return _find_cycles(self._dependencies)

    def validate(self) -> None:
        """Log a warning if the graph contains cycles."""
        cycles = self.has_cycles()
        if cycles:
            log.warning(
                "dependency_cycle_detected",
                extra={"cycles": cycles},
            )
        else:
            log.info("dependency_graph_validated", extra={"nodes": len(self._dependencies)})

    @classmethod
    def from_components(cls, components: list[ComponentSpec]) -> DependencyGraph:
        config = PhoenixConfig(components=components)
        return cls(config)


def _find_cycles(adjacency: dict[str, list[str]]) -> list[list[str]]:
    """Return every simple cycle in a directed graph (Tarjan SCC-based)."""
    cycles: list[list[str]] = []
    visited: set[str] = set()
    stack: list[str] = []
    in_stack: set[str] = set()

    def visit(node: str) -> None:
        if node in visited:
            return
        visited.add(node)
        stack.append(node)
        in_stack.add(node)
        for neighbor in adjacency.get(node, []):
            if neighbor not in visited:
                visit(neighbor)
            elif neighbor in in_stack:
                start = stack.index(neighbor)
                cycle = stack[start:] + [neighbor]
                if cycle not in cycles:
                    cycles.append(cycle)
        stack.pop()
        in_stack.discard(node)

    for node in adjacency:
        visit(node)
    return cycles
