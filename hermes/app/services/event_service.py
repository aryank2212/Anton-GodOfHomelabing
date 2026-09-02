from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.database.models import EventRecord
from app.models.event import EventCreate, EventList, EventResponse, Pagination, Severity

log = get_logger(__name__)


class EventService:
    """Create, store and query events. Never talks to notification providers."""

    def __init__(self, state: Any) -> None:
        self._settings = state.settings
        self._database = state.database
        self._queue = state.queue

    async def create(self, payload: EventCreate) -> EventResponse:
        correlation_id = payload.correlation_id or uuid4()
        event = EventRecord(
            module=payload.module,
            type=payload.type,
            severity=payload.severity.value,
            title=payload.title,
            message=payload.message,
            metadata_json=payload.metadata,
            tags=payload.tags,
            correlation_id=str(correlation_id),
        )

        async with self._database.session_factory() as session:
            session.add(event)
            await session.commit()

        # Non-blocking: the worker handles rules + delivery asynchronously.
        self._queue.put(event.id)

        log.info(
            "event_queued",
            extra={
                "event_id": event.id,
                "event_module": event.module,
                "type": event.type,
                "severity": event.severity,
            },
        )
        return EventResponse(id=UUID(event.id), status="queued")

    async def list_events(
        self,
        *,
        module: str | None,
        type_: str | None,
        severity: Severity | None,
        limit: int,
        offset: int,
    ) -> EventList:
        limit = min(limit, self._settings.max_pagination_limit)

        filters = []
        if module:
            filters.append(EventRecord.module == module)
        if type_:
            filters.append(EventRecord.type == type_)
        if severity:
            filters.append(EventRecord.severity == severity.value)

        async with self._database.session_factory() as session:
            total = await self._count(session, filters)
            rows = await self._select(session, filters, limit, offset)

        items = [event.to_event_data() for event in rows]
        next_offset = offset + limit if offset + limit < total else None
        return EventList(
            items=items,
            pagination=Pagination(limit=limit, offset=offset, total=total, next_offset=next_offset),
        )

    async def _count(self, session: AsyncSession, filters: list) -> int:
        statement = select(func.count()).select_from(EventRecord)
        if filters:
            statement = statement.where(*filters)
        return (await session.execute(statement)).scalar_one()

    async def _select(
        self, session: AsyncSession, filters: list, limit: int, offset: int
    ) -> list[EventRecord]:
        statement = select(EventRecord).order_by(EventRecord.timestamp.desc(), EventRecord.id)
        if filters:
            statement = statement.where(*filters)
        rows = (await session.execute(statement.offset(offset).limit(limit))).scalars().all()
        return list(rows)
