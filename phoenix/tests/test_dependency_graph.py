from __future__ import annotations

from app.config.models import ComponentSpec, PhoenixConfig
from app.services.dependency_graph import DependencyGraph


def _graph():
    config = PhoenixConfig(
        components=[
            ComponentSpec(name="docker", monitors=[]),
            ComponentSpec(name="postgres", monitors=[], depends_on=["docker"]),
            ComponentSpec(name="paperless", monitors=[], depends_on=["postgres"]),
            ComponentSpec(name="jellyfin", monitors=[], depends_on=["docker"]),
        ]
    )
    return DependencyGraph(config)


def test_dependents_of() -> None:
    graph = _graph()
    assert set(graph.dependents_of("docker")) == {"postgres", "jellyfin"}
    assert graph.dependents_of("postgres") == ["paperless"]
    assert graph.dependents_of("paperless") == []


def test_dependencies_of() -> None:
    graph = _graph()
    assert graph.dependencies_of("paperless") == ["postgres"]
    assert graph.dependencies_of("docker") == []
    assert graph.dependencies_of("unknown") == []


def test_acyclic_graph_has_no_cycles() -> None:
    assert _graph().has_cycles() == []


def test_cycle_detection() -> None:
    config = PhoenixConfig(
        components=[
            ComponentSpec(name="a", monitors=[], depends_on=["b"]),
            ComponentSpec(name="b", monitors=[], depends_on=["a"]),
        ]
    )
    cycles = DependencyGraph(config).has_cycles()
    assert cycles, "expected a cycle to be detected"
    assert {"a", "b"} <= {node for cycle in cycles for node in cycle}


def test_validate_does_not_raise() -> None:
    _graph().validate()
