"""Repository — all persistence for Sentinel.

Hides SQLAlchemy from the rest of the application: engines and API code only
see typed domain models. Timestamps round-trip as UTC (see
:mod:`app.database.session`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.database.models import (
    DeviceHistoryRecord,
    DeviceRecord,
    ObservationRecord,
    PresenceRecord,
    SituationRecord,
)
from app.models.device import Device, DeviceEvent, DeviceKind, PresenceState, PresenceStatus
from app.models.observation import Category, Observation, Severity
from app.models.situation import Situation, SituationStatus

log = get_logger(__name__)


def _store_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC).replace(tzinfo=None)


def _load_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC)


def _load_required(value: datetime | None) -> datetime:
    if value is None:
        raise ValueError("unexpected null value for required timestamp")
    return value.replace(tzinfo=UTC)


def _observation_to_row(observation: Observation) -> dict[str, object]:
    return {
        "observation_id": str(observation.observation_id),
        "timestamp": _store_utc(observation.timestamp),
        "source": observation.source,
        "category": observation.category.value,
        "severity": observation.severity.value,
        "object_": observation.object,
        "state": observation.state,
        "confidence": observation.confidence,
        "metadata_": observation.metadata,
        "tags": observation.tags,
        "tags_text": " ".join(observation.tags),
    }


def _observation_from_row(row: ObservationRecord) -> Observation:
    return Observation(
        observation_id=UUID(row.observation_id),
        timestamp=_load_required(row.timestamp),
        source=row.source,
        category=Category(row.category),
        severity=Severity(row.severity),
        object=row.object_,
        state=row.state,
        confidence=row.confidence,
        metadata=dict(row.metadata_ or {}),
        tags=list(row.tags or []),
    )


def _situation_from_row(row: SituationRecord) -> Situation:
    return Situation(
        situation_id=UUID(row.situation_id),
        rule_id=row.rule_id,
        type=row.type,
        name=row.name,
        status=SituationStatus(row.status),
        severity=Severity(row.severity),
        confidence=row.confidence,
        summary=row.summary,
        derived_from=[UUID(value) for value in (row.derived_from or [])],
        sources=list(row.sources or []),
        metadata=dict(row.metadata_ or {}),
        created_at=_load_required(row.created_at),
        updated_at=_load_required(row.updated_at),
        resolved_at=_load_utc(row.resolved_at),
    )


def _device_from_row(row: DeviceRecord) -> Device:
    return Device(
        device_key=row.device_key,
        mac=row.mac,
        name=row.name,
        known=row.known,
        owner=row.owner,
        category=DeviceKind(row.category) if row.category else DeviceKind.UNKNOWN,
        vendor=row.vendor,
        ips=list(row.ips or []),
        hostnames=list(row.hostnames or []),
        first_seen=_load_utc(row.first_seen),
        last_seen=_load_utc(row.last_seen),
        online=row.online,
        confidence=row.confidence,
        metadata=dict(row.metadata_ or {}),
        updated_at=_load_required(row.updated_at),
    )


def _presence_from_row(row: PresenceRecord) -> PresenceState:
    return PresenceState(
        status=PresenceStatus(row.status),
        confidence=row.confidence,
        people=list(row.people or []),
        devices_online=list(row.devices_online or []),
        unknown_devices=list(row.unknown_devices or []),
        timestamp=_load_required(row.timestamp),
        metadata=dict(row.metadata_ or {}),
    )


class Repository:
    """Persistence surface. Sessions are opened per operation."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session = session_factory

    # ------------------------------------------------------------ observations
    async def add_observation(self, observation: Observation) -> None:
        async with self._session() as session:
            session.add(ObservationRecord(**_observation_to_row(observation)))
            await session.commit()

    async def get_observation(self, observation_id: UUID) -> Observation | None:
        async with self._session() as session:
            row = await session.get(ObservationRecord, str(observation_id))
            return _observation_from_row(row) if row else None

    async def list_observations(
        self,
        *,
        source: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        object: str | None = None,
        state: str | None = None,
        tag: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Observation], int]:
        async with self._session() as session:
            stmt = select(ObservationRecord)
            if source:
                stmt = stmt.where(ObservationRecord.source == source)
            if category:
                stmt = stmt.where(ObservationRecord.category == category)
            if severity:
                stmt = stmt.where(ObservationRecord.severity == severity)
            if object:
                stmt = stmt.where(ObservationRecord.object_ == object)
            if state:
                stmt = stmt.where(ObservationRecord.state == state)
            if tag:
                stmt = stmt.where(ObservationRecord.tags_text.like(f"%{tag}%"))
            if since:
                stmt = stmt.where(ObservationRecord.timestamp >= _store_utc(since))
            if until:
                stmt = stmt.where(ObservationRecord.timestamp <= _store_utc(until))
            total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (
                (
                    await session.execute(
                        stmt.order_by(
                            ObservationRecord.timestamp.desc(),
                            ObservationRecord.observation_id.desc(),
                        )
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            return [_observation_from_row(row) for row in rows], total or 0

    async def recent_observations(self, *, days: int = 1, limit: int = 10_000) -> list[Observation]:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        async with self._session() as session:
            rows = (
                (
                    await session.execute(
                        select(ObservationRecord)
                        .where(ObservationRecord.timestamp >= _store_utc(cutoff))
                        .order_by(ObservationRecord.timestamp.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return [_observation_from_row(row) for row in rows]

    async def count_observations(self) -> int:
        async with self._session() as session:
            return await session.scalar(select(func.count()).select_from(ObservationRecord)) or 0

    # -------------------------------------------------------------- situations
    async def save_situation(self, situation: Situation) -> None:
        data = situation.to_record_dict()
        async with self._session() as session:
            record = await session.get(SituationRecord, str(situation.situation_id))
            if record is None:
                session.add(SituationRecord(**data))
            else:
                for key, value in data.items():
                    setattr(record, key, value)
            await session.commit()

    async def get_situation(self, situation_id: UUID) -> Situation | None:
        async with self._session() as session:
            row = await session.get(SituationRecord, str(situation_id))
            return _situation_from_row(row) if row else None

    async def list_situations(
        self,
        *,
        rule_id: str | None = None,
        type: str | None = None,
        status: str | None = None,
        severity: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Situation], int]:
        async with self._session() as session:
            stmt = select(SituationRecord)
            if rule_id:
                stmt = stmt.where(SituationRecord.rule_id == rule_id)
            if type:
                stmt = stmt.where(SituationRecord.type == type)
            if status:
                stmt = stmt.where(SituationRecord.status == status)
            if severity:
                stmt = stmt.where(SituationRecord.severity == severity)
            if since:
                stmt = stmt.where(SituationRecord.created_at >= _store_utc(since))
            if until:
                stmt = stmt.where(SituationRecord.created_at <= _store_utc(until))
            total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (
                (
                    await session.execute(
                        stmt.order_by(
                            SituationRecord.updated_at.desc(),
                            SituationRecord.situation_id.desc(),
                        )
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            return [_situation_from_row(row) for row in rows], total or 0

    async def active_situations(self) -> list[Situation]:
        async with self._session() as session:
            rows = (
                (
                    await session.execute(
                        select(SituationRecord)
                        .where(SituationRecord.status == SituationStatus.ACTIVE.value)
                        .order_by(SituationRecord.updated_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            return [_situation_from_row(row) for row in rows]

    async def count_situations(self, status: str | None = None) -> int:
        async with self._session() as session:
            stmt = select(func.count()).select_from(SituationRecord)
            if status:
                stmt = stmt.where(SituationRecord.status == status)
            return await session.scalar(stmt) or 0

    # ----------------------------------------------------------------- devices
    async def upsert_device(self, device: Device) -> None:
        data = device.to_record_dict()
        async with self._session() as session:
            record = await session.get(DeviceRecord, device.device_key)
            if record is None:
                session.add(DeviceRecord(**data))
            else:
                for key, value in data.items():
                    setattr(record, key, value)
            await session.commit()

    async def upsert_devices(self, devices: list[Device]) -> None:
        for device in devices:
            await self.upsert_device(device)

    async def get_device(self, device_key: str) -> Device | None:
        async with self._session() as session:
            row = await session.get(DeviceRecord, device_key)
            return _device_from_row(row) if row else None

    async def list_devices(
        self,
        *,
        known: bool | None = None,
        online: bool | None = None,
        category: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Device], int]:
        async with self._session() as session:
            stmt = select(DeviceRecord)
            if known is not None:
                stmt = stmt.where(DeviceRecord.known == known)
            if online is not None:
                stmt = stmt.where(DeviceRecord.online == online)
            if category:
                stmt = stmt.where(DeviceRecord.category == category)
            if q:
                pattern = f"%{q}%"
                stmt = stmt.where(
                    or_(
                        DeviceRecord.name.ilike(pattern),
                        DeviceRecord.mac.ilike(pattern),
                        DeviceRecord.device_key.ilike(pattern),
                        DeviceRecord.vendor.ilike(pattern),
                        DeviceRecord.owner.ilike(pattern),
                    )
                )
            total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (
                (
                    await session.execute(
                        stmt.order_by(DeviceRecord.last_seen.desc().nulls_last())
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            return [_device_from_row(row) for row in rows], total or 0

    async def count_devices(self) -> int:
        async with self._session() as session:
            return await session.scalar(select(func.count()).select_from(DeviceRecord)) or 0

    # ---------------------------------------------------------- device history
    async def add_device_event(self, event: DeviceEvent) -> None:
        async with self._session() as session:
            session.add(
                DeviceHistoryRecord(
                    device_key=event.device_key,
                    event=event.event,
                    timestamp=_store_utc(event.timestamp),
                    mac=event.mac,
                    ip=event.ip,
                    hostname=event.hostname,
                    source=event.source,
                    known=event.known,
                    metadata_=event.metadata,
                )
            )
            await session.commit()

    async def list_device_events(
        self,
        *,
        device_key: str | None = None,
        event: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, object]], int]:
        async with self._session() as session:
            stmt = select(DeviceHistoryRecord)
            if device_key:
                stmt = stmt.where(DeviceHistoryRecord.device_key == device_key)
            if event:
                stmt = stmt.where(DeviceHistoryRecord.event == event)
            total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (
                (
                    await session.execute(
                        stmt.order_by(
                            DeviceHistoryRecord.timestamp.desc(), DeviceHistoryRecord.id.desc()
                        )
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            items: list[dict[str, object]] = [
                {
                    "id": row.id,
                    "device_key": row.device_key,
                    "event": row.event,
                    "timestamp": _load_utc(row.timestamp),
                    "mac": row.mac,
                    "ip": row.ip,
                    "hostname": row.hostname,
                    "source": row.source,
                    "known": row.known,
                    "metadata": row.metadata_,
                }
                for row in rows
            ]
            return items, total or 0

    # -------------------------------------------------------------- presence
    async def add_presence_sample(self, state: PresenceState) -> None:
        data = state.to_record_dict()
        async with self._session() as session:
            session.add(PresenceRecord(**data))
            await session.commit()

    async def latest_presence(self) -> PresenceState | None:
        async with self._session() as session:
            row = (
                await session.execute(
                    select(PresenceRecord).order_by(PresenceRecord.timestamp.desc()).limit(1)
                )
            ).scalar_one_or_none()
            return _presence_from_row(row) if row else None

    async def list_presence_samples(
        self,
        *,
        status: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[PresenceState], int]:
        async with self._session() as session:
            stmt = select(PresenceRecord)
            if status:
                stmt = stmt.where(PresenceRecord.status == status)
            if since:
                stmt = stmt.where(PresenceRecord.timestamp >= _store_utc(since))
            if until:
                stmt = stmt.where(PresenceRecord.timestamp <= _store_utc(until))
            total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (
                (
                    await session.execute(
                        stmt.order_by(PresenceRecord.timestamp.desc(), PresenceRecord.id.desc())
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            return [_presence_from_row(row) for row in rows], total or 0
