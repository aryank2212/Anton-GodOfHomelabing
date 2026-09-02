from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.config.settings import Settings
from app.database.models import EventRecord
from app.database.session import Database
from app.services.storm import StormDetector
from sqlalchemy import select


class FakeQueue:
    def __init__(self) -> None:
        self.put_ids: list[str] = []

    def put(self, event_id: str) -> None:
        self.put_ids.append(event_id)

    def size(self) -> int:
        return len(self.put_ids)


def settings_for(tmp_path, **overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "_env_file": None,
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'storm.db'}",
        "storm_enabled": True,
        "storm_threshold": 3,
        "storm_window_seconds": 60,
        "storm_cooldown_seconds": 300,
    }
    defaults.update(overrides)
    return Settings(**defaults)


async def insert_event(database: Database, *, module: str, type_: str, timestamp: datetime) -> None:
    async with database.session_factory() as session:
        session.add(
            EventRecord(
                module=module,
                type=type_,
                severity="info",
                title="t",
                timestamp=timestamp,
                state="done",
            )
        )
        await session.commit()


async def storm_rows(database: Database) -> list[EventRecord]:
    async with database.session_factory() as session:
        rows = (
            (await session.execute(select(EventRecord).where(EventRecord.module == "hermes")))
            .scalars()
            .all()
        )
    return list(rows)


def now() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


async def test_emits_storm_when_threshold_reached(tmp_path) -> None:
    settings = settings_for(tmp_path)
    database = Database(settings.database_url)
    await database.init()
    queue = FakeQueue()
    stamp = now()
    try:
        for _ in range(3):
            await insert_event(database, module="watcher", type_="disk.usage", timestamp=stamp)

        detector = StormDetector(settings, database=database, queue=queue)
        emitted = await detector.check_once(stamp)

        assert len(emitted) == 1
        assert queue.put_ids == emitted
        storms = await storm_rows(database)
        assert len(storms) == 1
        assert storms[0].type == "event.storm"
        assert storms[0].severity == "warning"
        assert storms[0].metadata_json["count"] == 3
        assert storms[0].metadata_json["source_module"] == "watcher"
        assert "storm" in storms[0].tags
    finally:
        await database.dispose()


async def test_below_threshold_no_storm(tmp_path) -> None:
    settings = settings_for(tmp_path)
    database = Database(settings.database_url)
    await database.init()
    queue = FakeQueue()
    stamp = now()
    try:
        for _ in range(2):  # threshold is 3
            await insert_event(database, module="watcher", type_="disk.usage", timestamp=stamp)

        detector = StormDetector(settings, database=database, queue=queue)
        assert await detector.check_once(stamp) == []
        assert queue.put_ids == []
        assert await storm_rows(database) == []
    finally:
        await database.dispose()


async def test_cooldown_prevents_immediate_restorm(tmp_path) -> None:
    settings = settings_for(tmp_path, storm_window_seconds=100000)
    database = Database(settings.database_url)
    await database.init()
    queue = FakeQueue()
    stamp = now()
    try:
        for _ in range(3):
            await insert_event(database, module="watcher", type_="disk.usage", timestamp=stamp)

        detector = StormDetector(settings, database=database, queue=queue)
        assert len(await detector.check_once(stamp)) == 1
        # Same window, still in cooldown -> no new storm.
        assert await detector.check_once(stamp) == []
        # Cooldown expired -> new storm.
        later = stamp + timedelta(seconds=301)
        assert len(await detector.check_once(later)) == 1
    finally:
        await database.dispose()


async def test_hermes_events_are_excluded(tmp_path) -> None:
    settings = settings_for(tmp_path)
    database = Database(settings.database_url)
    await database.init()
    queue = FakeQueue()
    stamp = now()
    try:
        for _ in range(5):
            await insert_event(database, module="hermes", type_="event.storm", timestamp=stamp)

        detector = StormDetector(settings, database=database, queue=queue)
        assert await detector.check_once(stamp) == []
        assert queue.put_ids == []
    finally:
        await database.dispose()


async def test_grouping_by_module_and_type(tmp_path) -> None:
    settings = settings_for(tmp_path)
    database = Database(settings.database_url)
    await database.init()
    queue = FakeQueue()
    stamp = now()
    try:
        for _ in range(3):
            await insert_event(database, module="watcher", type_="disk.usage", timestamp=stamp)
        for _ in range(3):
            await insert_event(database, module="watcher", type_="cpu.usage", timestamp=stamp)

        detector = StormDetector(settings, database=database, queue=queue)
        emitted = await detector.check_once(stamp)
        assert len(emitted) == 2
    finally:
        await database.dispose()
