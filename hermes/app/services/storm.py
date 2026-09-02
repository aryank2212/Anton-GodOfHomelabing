from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.config.settings import Settings
from app.core.logging import get_logger
from app.core.queue import NotificationQueue
from app.database.models import EventRecord
from app.database.session import Database

log = get_logger(__name__)


class StormDetector:
    """Detects bursts of repeated events and collapses them into one storm event.

    Every ``check_interval`` seconds the detector counts events grouped by
    ``(module, type)`` over a rolling ``window``. Any group that reached
    ``threshold`` events is re-emitted as a single ``hermes/event.storm``
    event which flows through the normal rules and notifications pipeline.
    A per-group cooldown prevents re-firing while the storm is ongoing.

    Storms are emitted as events so operators can rule on them (e.g. notify
    Telegram only when a storm happens, instead of being spammed per event).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        database: Database,
        queue: NotificationQueue,
        check_interval: float | None = None,
    ) -> None:
        self._settings = settings
        self._database = database
        self._queue = queue
        self._interval = check_interval or settings.storm_check_interval
        self._last_emitted: dict[str, datetime] = {}
        self._task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def enabled(self) -> bool:
        return self._settings.storm_enabled

    async def start(self) -> None:
        if not self.enabled:
            log.info("storm_detector_disabled")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="hermes-storm")
        log.info(
            "storm_detector_started",
            extra={
                "window_seconds": self._settings.storm_window_seconds,
                "threshold": self._settings.storm_threshold,
                "check_interval": self._interval,
            },
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.check_once()
            except Exception:
                log.exception("storm_check_failed")
            await asyncio.sleep(self._interval)

    async def check_once(self, now: datetime | None = None) -> list[str]:
        """Count recent events and emit storm events for groups over threshold.

        Returns the ids of the emitted storm events (empty when nothing fires).
        """
        now = now or datetime.now(UTC)
        emitted: list[str] = []
        for module, type_, count in await self._counts(now):
            if count < self._settings.storm_threshold:
                continue
            key = f"{module}:{type_}"
            last = self._last_emitted.get(key)
            cooldown = self._settings.storm_cooldown_seconds
            if last is not None and (now - last).total_seconds() < cooldown:
                continue
            self._last_emitted[key] = now
            emitted.append(await self._emit(module, type_, count))
        return emitted

    async def _counts(self, now: datetime) -> list[tuple[str, str, int]]:
        window = now - timedelta(seconds=self._settings.storm_window_seconds)
        async with self._database.session_factory() as session:
            rows = (
                await session.execute(
                    select(EventRecord.module, EventRecord.type, func.count())
                    .where(EventRecord.timestamp >= window, EventRecord.module != "hermes")
                    .group_by(EventRecord.module, EventRecord.type)
                )
            ).all()
        return [(module, type_, int(count)) for module, type_, count in rows]

    async def _emit(self, module: str, type_: str, count: int) -> str:
        event = EventRecord(
            module="hermes",
            type="event.storm",
            severity="warning",
            title=f"Event storm: {count} × {module}/{type_}",
            message=(
                f"{count} '{module}/{type_}' events within the last "
                f"{self._settings.storm_window_seconds:g}s. This may indicate a "
                f"failing loop or a service degrading fast."
            ),
            metadata_json={
                "source_module": module,
                "source_type": type_,
                "count": count,
                "window_seconds": self._settings.storm_window_seconds,
            },
            tags=["storm", module, type_],
        )
        async with self._database.session_factory() as session:
            session.add(event)
            await session.commit()
            event_id = event.id
        self._queue.put(event_id)
        log.warning(
            "storm_emitted",
            extra={
                "event_id": event_id,
                "event_module": module,
                "type": type_,
                "count": count,
                "window_seconds": self._settings.storm_window_seconds,
            },
        )
        return event_id
