"""Recovery orchestrator — Phoenix's core workflow.

    Health Check -> Failure Detected -> Create Incident
    -> Determine Recovery Strategy -> Execute Recovery
    -> Verify Recovery -> Close Incident -> Publish Event to Hermes

The orchestrator glues monitors, the recovery engine, the dependency graph,
incident persistence and the Hermes publisher together. It is deliberately
dependency-injected so tests can run the full workflow with fakes.
"""

from __future__ import annotations

from typing import Any

from app.config.models import ComponentSpec, MonitorSpec, PhoenixConfig
from app.core.clients import Clients
from app.core.logging import get_logger
from app.core.retry import RetryExhausted, run_with_retry
from app.models.check import MonitorResult
from app.models.event import (
    EVENT_RECOVERY_ESCALATED,
    EVENT_RECOVERY_SUCCESS,
    HermesEvent,
)
from app.models.incident import Incident, IncidentStatus
from app.monitors.base import Monitor
from app.monitors.registry import MonitorRegistry
from app.recovery.base import RecoveryResult, RecoveryStrategy
from app.recovery.registry import RecoveryRegistry
from app.services.dependency_graph import DependencyGraph
from app.services.hermes import EventPublisher
from app.services.incidents import IncidentService
from app.services.maintenance import MaintenanceService

log = get_logger(__name__)


class Orchestrator:
    def __init__(
        self,
        config: PhoenixConfig,
        clients: Clients,
        incidents: IncidentService,
        maintenance: MaintenanceService,
        publisher: EventPublisher,
        recovery_registry: RecoveryRegistry,
        monitor_registry: MonitorRegistry,
        graph: DependencyGraph,
    ) -> None:
        self.config = config
        self.clients = clients
        self.incidents = incidents
        self.maintenance = maintenance
        self.publisher = publisher
        self.recovery_registry = recovery_registry
        self.monitor_registry = monitor_registry
        self.graph = graph

    # ----------------------------------------------------------- monitoring
    async def handle_failure(self, monitor: Monitor, result: MonitorResult) -> None:
        """A monitor reported a failure: record an incident and try to recover."""
        spec = self.config.component_by_monitor(monitor.name)
        if spec is None:
            log.warning("monitor_unassigned", extra={"monitor": monitor.name})
            return
        monitor_spec = self._monitor_spec(monitor.name)

        if await self.maintenance.is_in_maintenance(spec.name):
            incident = await self.incidents.open(
                component=spec.name,
                failure_type=result.status,
                severity=monitor_spec.severity,
                detected_by="monitor",
                metadata=self._failure_metadata(monitor, result),
            )
            await self.incidents.mark_maintenance(incident.incident_id)
            return

        if await self.incidents.has_open(spec.name, result.status):
            log.debug(
                "open_incident_exists",
                extra={"component": spec.name, "failure_type": result.status},
            )
            return

        incident = await self.incidents.open(
            component=spec.name,
            failure_type=result.status,
            severity=monitor_spec.severity,
            detected_by="monitor",
            metadata=self._failure_metadata(monitor, result),
        )
        await self.incidents.add_event(
            incident.incident_id,
            "detect",
            "failure_detected",
            result.detail,
        )
        log.info(
            "failure_detected",
            extra={
                "incident_id": incident.incident_id,
                "component": incident.component,
                "failure_type": incident.failure_type,
                "monitor": monitor.name,
            },
        )
        await self.recover_incident(incident, verify_monitors=[monitor])

    async def handle_recovery(self, monitor: Monitor, result: MonitorResult) -> None:
        """A previously failing monitor is healthy again: close open incidents.

        Covers self-healing (the component recovered on its own, e.g. disk
        space freed or a process came back before Phoenix could act).
        """
        spec = self.config.component_by_monitor(monitor.name)
        if spec is None:
            return
        for incident in await self.incidents.open_for_component(spec.name):
            if incident.metadata.get("monitor") != monitor.name:
                continue
            log.info(
                "auto_recovered",
                extra={"incident_id": incident.incident_id, "component": spec.name},
            )
            await self._resolve(incident, strategy="auto", attempts=incident.attempts)
            await self._publish_recovery_success(incident, strategy="auto")

    # ------------------------------------------------------------ recovery
    async def recover_component(
        self,
        component: str,
        *,
        detected_by: str = "manual",
        failure_type: str = "manual_recovery",
        metadata: dict[str, Any] | None = None,
    ) -> Incident | None:
        """Manually trigger recovery for a component (``POST /recover``)."""
        spec = self.config.component(component)
        if spec is None:
            return None
        if await self.maintenance.is_in_maintenance(component):
            incident = await self.incidents.open(
                component=component,
                failure_type=failure_type,
                severity="warning",
                detected_by=detected_by,
                metadata={**(metadata or {}), "reason": "maintenance"},
            )
            await self.incidents.mark_maintenance(incident.incident_id)
            return incident

        incident = await self.incidents.open(
            component=component,
            failure_type=failure_type,
            severity=self._incident_severity(spec),
            detected_by=detected_by,
            metadata=metadata or {},
        )
        await self.incidents.add_event(
            incident.incident_id,
            "detect",
            "recovery_requested",
            f"manual recovery requested for '{component}'",
        )
        return await self.recover_incident(incident, verify_monitors=self._component_monitors(spec))

    async def recover_incident(
        self,
        incident: Incident,
        *,
        verify_monitors: list[Monitor] | None = None,
    ) -> Incident:
        """Run the full recovery workflow for an existing incident."""
        spec = self.config.component(incident.component)
        recovery = spec.recovery if spec is not None else None

        if spec is None or recovery is None:
            # Report-only component: no automated recovery is configured.
            await self.incidents.update_recovery_state(
                incident.incident_id,
                strategy=None,
                attempts=0,
                status=IncidentStatus.UNRESOLVED,
            )
            await self.incidents.add_event(
                incident.incident_id,
                "escalate",
                "no_recovery_configured",
                "no automated recovery strategy is configured",
            )
            await self._publish_recovery_failed(incident, strategy=None)
            return await self.incidents.get(incident.incident_id) or incident

        strategy = self.recovery_registry.build(recovery.strategy, recovery.params, self.clients)
        await self.incidents.update_recovery_state(
            incident.incident_id,
            strategy=strategy.name,
            attempts=0,
            status=IncidentStatus.RECOVERING,
        )

        attempts = 0
        result: RecoveryResult | None = None

        async def attempt(number: int) -> RecoveryResult:
            nonlocal attempts
            attempts = number
            result = await strategy.execute()
            await self.incidents.add_event(
                incident.incident_id,
                "recover",
                f"{strategy.name}_attempt",
                result.message,
            )
            return result

        try:
            result = await run_with_retry(
                recovery.retry_policy,
                attempt,
                component=incident.component,
                strategy=strategy.name,
            )
        except RetryExhausted as exc:
            attempts = exc.attempts

        recovered = await self._verify(recovery.verify, incident, strategy, verify_monitors)
        if recovered and result is not None and result.success:
            incident = await self._resolve(incident, strategy=strategy.name, attempts=attempts)
            await self._publish_recovery_success(incident, strategy=strategy.name)
            await self._restart_dependents(spec)
        else:
            incident = await self._fail(incident, strategy=strategy.name, attempts=attempts)
            await self._publish_recovery_failed(incident, strategy=strategy.name)
        return incident

    def _attempt_strategy(self, strategy: RecoveryStrategy, incident_id: str) -> Any:
        async def attempt(number: int) -> RecoveryResult:
            result = await strategy.execute()
            await self.incidents.add_event(
                incident_id,
                "recover",
                f"{strategy.name}_attempt",
                result.message,
            )
            return result

        return attempt

    async def _verify(
        self,
        verify: bool,
        incident: Incident,
        strategy: RecoveryStrategy,
        verify_monitors: list[Monitor] | None,
    ) -> bool:
        if not verify or not verify_monitors:
            return True
        ok = True
        for monitor in verify_monitors:
            result = await monitor.check()
            await self.incidents.add_event(
                incident.incident_id,
                "verify",
                f"verify_{monitor.name}",
                f"{monitor.name}: {result.status}",
            )
            if not result.ok:
                ok = False
        return ok

    async def _resolve(self, incident: Incident, *, strategy: str, attempts: int) -> Incident:
        resolved = await self.incidents.resolve(
            incident.incident_id, strategy=strategy, attempts=attempts
        )
        log.info(
            "incident_resolved",
            extra={
                "incident_id": resolved.incident_id,
                "component": resolved.component,
                "strategy": strategy,
                "duration": resolved.duration,
            },
        )
        return resolved

    async def _fail(self, incident: Incident, *, strategy: str, attempts: int) -> Incident:
        failed = await self.incidents.fail(
            incident.incident_id, strategy=strategy, attempts=attempts
        )
        log.warning(
            "incident_unresolved",
            extra={
                "incident_id": failed.incident_id,
                "component": failed.component,
                "strategy": strategy,
                "attempts": attempts,
            },
        )
        return failed

    # ------------------------------------------------------- dependency cascade
    async def _restart_dependents(self, component: ComponentSpec) -> None:
        """Gracefully restart services that depend on a recovered component."""
        for dependent_name in self.graph.dependents_of(component.name):
            dependent = self.config.component(dependent_name)
            if dependent is None or not dependent.restart_on_dependency_recovery:
                continue
            if await self.maintenance.is_in_maintenance(dependent_name):
                continue
            log.info(
                "dependency_recovery_started",
                extra={"dependency": component.name, "dependent": dependent_name},
            )
            incident = await self.incidents.open(
                component=dependent_name,
                failure_type="dependency_recovery",
                severity="warning",
                detected_by="dependency",
                metadata={"dependency": component.name},
            )
            await self.incidents.add_event(
                incident.incident_id,
                "detect",
                "dependency_restart",
                f"restarting '{dependent_name}' because '{component.name}' recovered",
            )
            await self.recover_incident(
                incident, verify_monitors=self._component_monitors(dependent)
            )

    # ------------------------------------------------------------ hermes events
    async def _publish_recovery_success(self, incident: Incident, strategy: str) -> None:
        event = HermesEvent(
            type=EVENT_RECOVERY_SUCCESS,
            severity="warning",
            title=f"{incident.component.title()} Recovered",
            message="Component recovered successfully.",
            metadata=self._event_metadata(incident, strategy),
            tags=["phoenix", "recovery", "success", incident.component],
            correlation_id=incident.correlation_id,
        )
        await self.publisher.publish(event)

    async def _publish_recovery_failed(self, incident: Incident, strategy: str | None) -> None:
        spec = self.config.component(incident.component)
        severity = (
            spec.recovery.escalate.severity
            if spec is not None and spec.recovery is not None
            else incident.severity
        )
        event = HermesEvent(
            type=EVENT_RECOVERY_ESCALATED,
            severity=severity,
            title=f"{incident.component.title()} Recovery Failed",
            message=f"Automated recovery exhausted after {incident.attempts} attempts.",
            metadata=self._event_metadata(incident, strategy),
            tags=["phoenix", "recovery", "escalated", incident.component],
            correlation_id=incident.correlation_id,
        )
        await self.publisher.publish(event)

    # ------------------------------------------------------------------ helpers
    def _monitor_spec(self, monitor_name: str) -> MonitorSpec:
        for monitor in self.config.monitors:
            if monitor.name == monitor_name:
                return monitor
        return MonitorSpec(name=monitor_name, type="http")

    def _component_monitors(self, spec: ComponentSpec) -> list[Monitor]:
        monitors: list[Monitor] = []
        for monitor_name in spec.monitors:
            monitor_spec = self._monitor_spec(monitor_name)
            if not monitor_spec.enabled:
                continue
            monitors.append(self.monitor_registry.build(monitor_spec, self.clients))
        return monitors

    def _failure_metadata(self, monitor: Monitor, result: MonitorResult) -> dict[str, Any]:
        return {
            "monitor": monitor.name,
            "status": result.status,
            "detail": result.detail,
            "metric": result.metric,
        }

    def _event_metadata(self, incident: Incident, strategy: str | None) -> dict[str, Any]:
        return {
            "incident_id": incident.incident_id,
            "component": incident.component,
            "failure_type": incident.failure_type,
            "detected_by": incident.detected_by,
            "strategy": strategy,
            "attempts": incident.attempts,
            "duration": incident.duration,
        }

    def _incident_severity(self, spec: ComponentSpec) -> str:
        if spec.recovery is not None:
            return spec.recovery.escalate.severity
        return "warning"
