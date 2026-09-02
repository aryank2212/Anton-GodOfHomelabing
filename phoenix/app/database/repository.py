"""Persistence for incidents, recovery timeline events and maintenance log."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.functions import count

from app.database import models as orm
from app.models.incident import Incident as IncidentDTO
from app.models.incident import IncidentStatus


def _dto(row: orm.Incident) -> IncidentDTO:
    return IncidentDTO(
        incident_id=row.incident_id,
        timestamp=_ensure_utc(row.timestamp),
        component=row.component,
        failure_type=row.failure_type,
        severity=row.severity,
        status=IncidentStatus(row.status),
        detected_by=row.detected_by,
        recovery_strategy=row.recovery_strategy,
        recovery_result=row.recovery_result,
        duration=row.duration,
        attempts=row.attempts,
        correlation_id=row.correlation_id,
        metadata=row.details or {},
    )


def _ensure_utc(value: datetime) -> datetime:
    """SQLite stores naive datetimes; assume they are UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class Repository:
    """Thin data-access layer over the Phoenix database."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # ------------------------------------------------------------------ incidents
    async def create_incident(self, incident: IncidentDTO) -> IncidentDTO:
        async with self._session_factory() as session:
            row = orm.Incident(
                incident_id=incident.incident_id,
                timestamp=incident.timestamp,
                component=incident.component,
                failure_type=incident.failure_type,
                severity=incident.severity,
                status=incident.status.value,
                detected_by=incident.detected_by,
                recovery_strategy=incident.recovery_strategy,
                recovery_result=incident.recovery_result,
                duration=incident.duration,
                attempts=incident.attempts,
                correlation_id=incident.correlation_id,
                details=incident.metadata,
            )
            session.add(row)
            await session.commit()
            return _dto(row)

    async def get_incident(self, incident_id: str) -> IncidentDTO | None:
        async with self._session_factory() as session:
            row = await session.get(orm.Incident, incident_id)
            return _dto(row) if row else None

    async def update_incident(self, incident_id: str, **fields: object) -> IncidentDTO | None:
        async with self._session_factory() as session:
            row = await session.get(orm.Incident, incident_id)
            if row is None:
                return None
            if "status" in fields and isinstance(fields["status"], str):
                row.status = fields["status"]
            for key in (
                "recovery_strategy",
                "recovery_result",
                "duration",
                "attempts",
                "correlation_id",
            ):
                if key in fields:
                    setattr(row, key, fields[key])
            await session.commit()
            return _dto(row)

    async def has_open_incident(self, component: str, failure_type: str) -> bool:
        async with self._session_factory() as session:
            stmt = select(orm.Incident.incident_id).where(
                orm.Incident.component == component,
                orm.Incident.failure_type == failure_type,
                orm.Incident.status.in_(["open", "recovering", "unresolved"]),
            )
            result = await session.execute(stmt.limit(1))
            return result.scalar_one_or_none() is not None

    async def open_incident_count(self) -> int:
        async with self._session_factory() as session:
            result = await session.execute(
                select(func.count())
                .select_from(orm.Incident)
                .where(orm.Incident.status.in_(["open", "recovering"]))
            )
            return int(result.scalar_one())

    async def open_incidents_for_component(self, component: str) -> list[IncidentDTO]:
        async with self._session_factory() as session:
            stmt = select(orm.Incident).where(
                orm.Incident.component == component,
                orm.Incident.status.in_(["open", "recovering", "unresolved"]),
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [_dto(r) for r in rows]

    async def list_incidents(
        self,
        *,
        component: str | None = None,
        status: str | None = None,
        severity: str | None = None,
        failure_type: str | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[IncidentDTO], int]:
        conditions = []
        if component:
            conditions.append(orm.Incident.component == component)
        if status:
            conditions.append(orm.Incident.status == status)
        if severity:
            conditions.append(orm.Incident.severity == severity)
        if failure_type:
            conditions.append(orm.Incident.failure_type == failure_type)

        async with self._session_factory() as session:
            total_stmt = select(func.count()).select_from(orm.Incident).where(*conditions)
            total = int((await session.execute(total_stmt)).scalar_one())

            stmt = (
                select(orm.Incident)
                .where(*conditions)
                .order_by(orm.Incident.timestamp.desc())
                .limit(limit)
                .offset(offset)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [_dto(r) for r in rows], total

    # ------------------------------------------------------- incident timeline
    async def add_event(
        self,
        incident_id: str,
        stage: str,
        step: str,
        message: str = "",
        duration: float | None = None,
    ) -> None:
        async with self._session_factory() as session:
            session.add(
                orm.IncidentEvent(
                    incident_id=incident_id,
                    stage=stage,
                    step=step,
                    message=message,
                    duration=duration,
                )
            )
            await session.commit()

    async def incident_timeline(self, incident_id: str) -> list[dict[str, object]]:
        async with self._session_factory() as session:
            stmt = (
                select(orm.IncidentEvent)
                .where(orm.IncidentEvent.incident_id == incident_id)
                .order_by(orm.IncidentEvent.timestamp.asc())
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [
                {
                    "timestamp": _ensure_utc(r.timestamp),
                    "stage": r.stage,
                    "step": r.step,
                    "message": r.message,
                    "duration": r.duration,
                }
                for r in rows
            ]

    # ----------------------------------------------------------------- stats
    async def count_incidents(self, *, status: str | None = None) -> int:
        conditions = [orm.Incident.status == status] if status else []
        async with self._session_factory() as session:
            result = await session.execute(
                select(func.count()).select_from(orm.Incident).where(*conditions)
            )
            return int(result.scalar_one())

    async def mean_recovery_time(self) -> float | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(func.avg(orm.Incident.duration)).where(
                    orm.Incident.status == "resolved", orm.Incident.duration.is_not(None)
                )
            )
            value = result.scalar_one()
            return float(value) if value is not None else None

    async def incidents_by_component(self, limit: int | None = None) -> list[tuple[str, int]]:
        return await self._group_by(orm.Incident.component, limit)

    async def unresolved_by_component(self, limit: int | None = None) -> list[tuple[str, int]]:
        return await self._group_by(orm.Incident.component, limit, status="unresolved")

    async def incidents_by_day(self) -> list[tuple[str, int]]:
        async with self._session_factory() as session:
            stmt = (
                select(
                    func.date(orm.Incident.timestamp).label("day"),
                    count().label("n"),
                )
                .group_by("day")
                .order_by("day")
            )
            rows = (await session.execute(stmt)).all()
            return [(str(r[0]), int(r[1])) for r in rows]

    async def recoveries_by_strategy(self) -> list[tuple[str, int]]:
        return await self._group_by(
            orm.Incident.recovery_strategy,
            where_not_null=orm.Incident.recovery_strategy,
        )

    async def recoveries_by_component(self) -> list[tuple[str, int]]:
        return await self._group_by(
            orm.Incident.component, where_not_null=orm.Incident.recovery_strategy
        )

    async def _group_by(
        self,
        column: InstrumentedAttribute[Any],
        limit: int | None = None,
        *,
        status: str | None = None,
        where_not_null: InstrumentedAttribute[Any] | None = None,
    ) -> list[tuple[str, int]]:
        conditions = []
        if status:
            conditions.append(orm.Incident.status == status)
        if where_not_null is not None:
            conditions.append(where_not_null.is_not(None))
        stmt = select(column, count()).where(*conditions).group_by(column).order_by(count().desc())
        if limit:
            stmt = stmt.limit(limit)
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).all()
            return [(str(r[0]) if r[0] is not None else "unknown", int(r[1])) for r in rows]

    # -------------------------------------------------------------- maintenance
    async def active_maintenance_for(self, component: str) -> bool:
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            stmt = select(orm.Maintenance.id).where(
                orm.Maintenance.component == component,
                orm.Maintenance.active.is_(True),
                orm.Maintenance.start_time <= now,
                (orm.Maintenance.end_time.is_(None)) | (orm.Maintenance.end_time >= now),
            )
            result = await session.execute(stmt.limit(1))
            return result.scalar_one_or_none() is not None

    async def create_maintenance(
        self,
        *,
        component: str,
        start_time: datetime,
        end_time: datetime | None,
        reason: str,
    ) -> dict[str, object]:
        async with self._session_factory() as session:
            row = orm.Maintenance(
                component=component,
                start_time=start_time,
                end_time=end_time,
                reason=reason,
                active=True,
            )
            session.add(row)
            await session.commit()
            return {
                "id": row.id,
                "component": row.component,
                "start_time": row.start_time,
                "end_time": row.end_time,
                "reason": row.reason,
                "active": row.active,
            }

    async def list_maintenance(self, limit: int, offset: int) -> list[dict[str, object]]:
        async with self._session_factory() as session:
            stmt = (
                select(orm.Maintenance)
                .order_by(orm.Maintenance.start_time.desc())
                .limit(limit)
                .offset(offset)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [
                {
                    "id": r.id,
                    "component": r.component,
                    "start_time": _ensure_utc(r.start_time),
                    "end_time": _ensure_utc(r.end_time) if r.end_time else None,
                    "reason": r.reason,
                    "created_at": _ensure_utc(r.created_at),
                    "active": r.active,
                }
                for r in rows
            ]

    async def close_maintenance(self, maintenance_id: int) -> bool:
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            result: CursorResult[Any] = await session.execute(  # type: ignore[assignment]
                update(orm.Maintenance)
                .where(orm.Maintenance.id == maintenance_id)
                .values(active=False, end_time=now)
            )
            await session.commit()
            return bool(result.rowcount)
