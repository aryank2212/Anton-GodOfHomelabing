"""Pydantic models for the YAML-driven Phoenix configuration.

The YAML file describes *what to monitor* and *how to recover*. See
``app/config/phoenix.yaml`` for a fully documented example.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.retry import RetryPolicy


class SchedulerSpec(BaseModel):
    """Global scheduler tuning (can be overridden by the ``PHOENIX_`` env)."""

    tick_interval: float | None = None
    max_concurrent_checks: int | None = None


class MonitorSpec(BaseModel):
    """A single health check.

    ``type`` selects the monitor implementation (``docker``, ``systemd``,
    ``http``, ``disk``, ``memory``, ``cpu``, ``network``) and ``params`` are
    the type-specific parameters. Adding a monitor type means adding a
    ``Monitor`` subclass and registering it in the monitor registry.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    type: Literal["docker", "systemd", "http", "disk", "memory", "cpu", "network"]
    interval: float = Field(default=60.0, gt=0)
    enabled: bool = True
    severity: str = Field(default="warning", pattern=r"^(debug|info|warning|error|critical)$")
    params: dict[str, Any] = Field(default_factory=dict)


class EscalationSpec(BaseModel):
    """What happens when automated recovery is exhausted."""

    severity: str = Field(default="critical", pattern=r"^(debug|info|warning|error|critical)$")


class RecoverySpec(BaseModel):
    """Recovery behaviour for one component.

    ``strategy`` selects the recovery strategy (``docker_restart``,
    ``systemd_restart``, ``http_retry``, ``noop``). ``retry`` drives the
    attempt schedule and ``escalate`` describes the event raised when the
    final attempt fails. When ``verify`` is true the failing monitor is
    re-run after recovery to confirm the component is healthy again.
    """

    strategy: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    retry: dict[str, Any] = Field(default_factory=dict)
    escalate: EscalationSpec = Field(default_factory=EscalationSpec)
    verify: bool = True

    @property
    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy.from_dict(self.retry)


class ComponentSpec(BaseModel):
    """One monitored component of Anton.

    ``monitors`` lists monitor names owned by this component; when any of
    them fails an incident is raised for the component and its recovery
    policy is executed. ``depends_on`` drives the dependency graph.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    monitors: list[str] = Field(default_factory=list)
    recovery: RecoverySpec | None = None
    depends_on: list[str] = Field(default_factory=list)
    restart_on_dependency_recovery: bool = True


class PhoenixConfig(BaseModel):
    """Top-level YAML document."""

    version: int = 1
    scheduler: SchedulerSpec = Field(default_factory=SchedulerSpec)
    monitors: list[MonitorSpec] = Field(default_factory=list)
    components: list[ComponentSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_references(self) -> PhoenixConfig:
        monitor_names = {m.name for m in self.monitors}
        component_names = {c.name for c in self.components}
        for component in self.components:
            missing = [m for m in component.monitors if m not in monitor_names]
            if missing:
                raise ValueError(
                    f"component '{component.name}' references unknown monitors: {missing}"
                )
            for dependency in component.depends_on:
                if dependency not in component_names:
                    raise ValueError(
                        f"component '{component.name}' depends on unknown component: '{dependency}'"
                    )
        assigned = {m for c in self.components for m in c.monitors}
        unassigned = monitor_names - assigned
        if unassigned:
            raise ValueError(f"monitors not assigned to any component: {sorted(unassigned)}")
        return self

    def component_by_monitor(self, monitor_name: str) -> ComponentSpec | None:
        for component in self.components:
            if monitor_name in component.monitors:
                return component
        return None

    def component(self, name: str) -> ComponentSpec | None:
        for component in self.components:
            if component.name == name:
                return component
        return None
