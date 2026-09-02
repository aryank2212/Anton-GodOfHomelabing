"""Incident lifecycle — creation, updates, queries, recovery timeline."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.logging import get_logger
from app.database.repository import Repository
from app.models.incident import Incident, IncidentCreate, IncidentStatus, IncidentUpdate

log = get_logger(__name__)


class IncidentService:
    """Owns the incident lifecycle used by the orchestrator and the API."""

    def __init__(self, repository: Repository) -> None:
        self._repo = repository

    async def open(
        self,
        *,
        component: str,
        failure_type: str,
        severity: str = "warning",
        detected_by: str = "monitor",
        metadata: dict | None = None,
    ) -> Incident:
        incident = Incident(
            **IncidentCreate(
                component=component,
                failure_type=failure_type,
                severity=severity,
                detected_by=detected_by,
                metadata=metadata or {},
            ).model_dump()
        )
        incident.correlation_id = incident.incident_id
        stored = await self._repo.create_incident(incident)
        log.info(
            "incident_opened",
            extra={
                "incident_id": stored.incident_id,
                "component": stored.component,
                "failure_type": stored.failure_type,
                "severity": stored.severity,
                "detected_by": stored.detected_by,
            },
        )
        return stored

    async def get(self, incident_id: str) -> Incident | None:
        return await self._repo.get_incident(incident_id)

    async def update(self, incident_id: str, update: IncidentUpdate) -> Incident | None:
        fields = update.model_dump(exclude_none=True)
        if not fields:
            return await self._repo.get_incident(incident_id)
        updated = await self._repo.update_incident(incident_id, **fields)
        if updated:
            log.info(
                "incident_updated",
                extra={
                    "incident_id": updated.incident_id,
                    "status": updated.status,
                    "recovery_result": updated.recovery_result,
                    "duration": updated.duration,
                },
            )
        return updated

    async def has_open(self, component: str, failure_type: str) -> bool:
        return await self._repo.has_open_incident(component, failure_type)

    async def open_count(self) -> int:
        return await self._repo.open_incident_count()

    async def open_for_component(self, component: str) -> list[Incident]:
        return await self._repo.open_incidents_for_component(component)

    async def update_recovery_state(
        self,
        incident_id: str,
        *,
        strategy: str | None,
        attempts: int,
        status: IncidentStatus,
    ) -> Incident:
        incident = await self.update(
            incident_id,
            IncidentUpdate(
                status=status,
                recovery_strategy=strategy,
                attempts=attempts,
            ),
        )
        assert incident is not None
        return incident

    async def search(
        self,
        *,
        component: str | None = None,
        status: str | None = None,
        severity: str | None = None,
        failure_type: str | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[Incident], int]:
        return await self._repo.list_incidents(
            component=component,
            status=status,
            severity=severity,
            failure_type=failure_type,
            limit=limit,
            offset=offset,
        )

    async def timeline(self, incident_id: str) -> list[dict[str, object]]:
        return await self._repo.incident_timeline(incident_id)

    async def add_event(
        self,
        incident_id: str,
        stage: str,
        step: str,
        message: str = "",
        duration: float | None = None,
    ) -> None:
        await self._repo.add_event(incident_id, stage, step, message, duration)

    async def resolve(
        self,
        incident_id: str,
        *,
        strategy: str,
        attempts: int,
    ) -> Incident:
        duration = await self._measure_duration(incident_id)
        incident = await self.update(
            incident_id,
            IncidentUpdate(
                status=IncidentStatus.RESOLVED,
                recovery_strategy=strategy,
                recovery_result=True,
                duration=duration,
                attempts=attempts,
            ),
        )
        assert incident is not None
        await self.add_event(
            incident_id,
            "close",
            "incident_closed",
            f"recovered via '{strategy}' in {duration:.1f}s",
            duration=duration,
        )
        return incident

    async def fail(
        self,
        incident_id: str,
        *,
        strategy: str | None,
        attempts: int,
    ) -> Incident:
        incident = await self.update(
            incident_id,
            IncidentUpdate(
                status=IncidentStatus.UNRESOLVED,
                recovery_strategy=strategy,
                recovery_result=False,
                attempts=attempts,
            ),
        )
        assert incident is not None
        await self.add_event(
            incident_id,
            "escalate",
            "recovery_failed",
            f"recovery '{strategy or 'none'}' exhausted after {attempts} attempts",
        )
        return incident

    async def mark_maintenance(self, incident_id: str) -> Incident:
        incident = await self.update(
            incident_id,
            IncidentUpdate(status=IncidentStatus.MAINTENANCE, recovery_result=None),
        )
        assert incident is not None
        await self.add_event(
            incident_id,
            "detect",
            "maintenance_window",
            "failure recorded during a maintenance window; recovery skipped",
        )
        return incident

    async def _measure_duration(self, incident_id: str) -> float:
        incident = await self.get(incident_id)
        if incident is None:
            return 0.0
        now = datetime.now(UTC)
        return (now - incident.timestamp).total_seconds()
