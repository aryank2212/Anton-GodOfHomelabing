from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from app.config.settings import Settings
from app.core.logging import get_logger
from app.core.queue import NotificationQueue
from app.core.renderer import Renderer
from app.database.models import EventRecord
from app.database.session import Database
from app.providers.base import BaseProvider, ProviderError, ProviderMessage
from app.providers.registry import ProviderRegistry
from app.rules.engine import RuleEngine
from app.rules.loader import load_rules
from app.services.dispatcher import Dispatcher

log = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "app" / "templates"

NOTIFY_YAML = """
version: 1
rules:
  - name: "notify_errors"
    action: notify
    when:
      severity: "error"
    providers: ["fake"]

  - name: "default"
    action: log
    when: {}
"""


class FakeProvider(BaseProvider):
    name = "fake"
    templates: ClassVar[dict[str, str]] = {"text": "telegram.j2"}

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.sent: list[ProviderMessage] = []
        self.should_fail = False

    @property
    def enabled(self) -> bool:
        return True

    async def send(self, message: ProviderMessage) -> None:
        if self.should_fail:
            raise ProviderError("boom")
        self.sent.append(message)


def build_settings(tmp_path: Path, rules_yaml: str, **overrides: Any) -> Settings:
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(rules_yaml, encoding="utf-8")
    base = {
        "_env_file": None,
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'hermes.db'}",
        "rules_file": str(rules_file),
        "templates_dir": str(TEMPLATES_DIR),
        "notification_max_attempts": 1,
        "notification_retry_base_delay": 0.0,
    }
    base.update(overrides)
    return Settings(**base)


async def make_dispatcher(
    settings: Settings, providers: list[BaseProvider] | None = None
) -> tuple[Dispatcher, Database, list[BaseProvider]]:
    database = Database(settings.database_url)
    await database.init()

    registry = ProviderRegistry(settings)
    providers = providers or []
    for provider in providers:
        registry.register(provider)

    engine = RuleEngine(load_rules(settings.rules_file))
    renderer = Renderer(settings.templates_dir)
    dispatcher = Dispatcher(
        settings=settings,
        database=database,
        registry=registry,
        engine=engine,
        renderer=renderer,
    )
    return dispatcher, database, providers


async def insert_event(database: Database, **fields: Any) -> str:
    async with database.session_factory() as session:
        event = EventRecord(
            module=fields.get("module", "watcher"),
            type=fields.get("type", "disk.usage"),
            severity=fields.get("severity", "error"),
            title=fields.get("title", "Disk usage high"),
            message=fields.get("message", "boom"),
            state=fields.get("state", "pending"),
        )
        session.add(event)
        await session.commit()
        return event.id


async def get_state(database: Database, event_id: str) -> str:
    async with database.session_factory() as session:
        event = await session.get(EventRecord, event_id)
    return event.state if event else "missing"


async def get_outcome(database: Database, event_id: str) -> str | None:
    async with database.session_factory() as session:
        event = await session.get(EventRecord, event_id)
    return event.outcome if event else None


async def get_notifications(database: Database, event_id: str) -> list:
    from app.database.models import NotificationRecord
    from sqlalchemy import select

    async with database.session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(NotificationRecord).where(NotificationRecord.event_id == event_id)
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


async def db_state_is(database: Database, event_id: str, expected: str) -> bool:
    return (await get_state(database, event_id)) == expected


async def test_dispatcher_sends_and_records_notification(tmp_path) -> None:
    settings = build_settings(tmp_path, NOTIFY_YAML)
    dispatcher, database, providers = await make_dispatcher(settings, [FakeProvider(settings)])
    try:
        event_id = await insert_event(database)
        await dispatcher.process(event_id)

        assert (await get_state(database, event_id)) == "done"
        assert (await get_outcome(database, event_id)) == "notified"
        notifications = await get_notifications(database, event_id)
        assert len(notifications) == 1
        assert notifications[0].provider == "fake"
        assert notifications[0].status == "sent"
        assert notifications[0].attempts == 1
        assert providers[0].sent
    finally:
        await database.dispose()


async def test_dispatcher_marks_failed_delivery(tmp_path) -> None:
    settings = build_settings(tmp_path, NOTIFY_YAML)
    fake = FakeProvider(settings)
    fake.should_fail = True
    dispatcher, database, providers = await make_dispatcher(settings, [fake])
    try:
        event_id = await insert_event(database)
        await dispatcher.process(event_id)

        assert (await get_outcome(database, event_id)) == "failed"
        notifications = await get_notifications(database, event_id)
        assert notifications[0].status == "failed"
        assert notifications[0].error
        assert not providers[0].sent
    finally:
        await database.dispose()


async def test_dispatcher_ignores_and_logs(tmp_path) -> None:
    settings = build_settings(
        tmp_path,
        """
version: 1
rules:
  - name: "ignore_debug"
    action: ignore
    when:
      severity: "debug"
  - name: "log_all"
    action: log
    when: {}
""",
    )
    dispatcher, database, _ = await make_dispatcher(settings)
    try:
        ignored_id = await insert_event(database, severity="debug")
        await dispatcher.process(ignored_id)
        assert (await get_outcome(database, ignored_id)) == "ignored"

        logged_id = await insert_event(database, severity="warning")
        await dispatcher.process(logged_id)
        assert (await get_outcome(database, logged_id)) == "logged"
        assert (await get_notifications(database, logged_id)) == []
    finally:
        await database.dispose()


async def test_dispatcher_does_not_double_process(tmp_path) -> None:
    settings = build_settings(tmp_path, NOTIFY_YAML)
    fake = FakeProvider(settings)
    dispatcher, database, _ = await make_dispatcher(settings, [fake])
    try:
        event_id = await insert_event(database)
        await dispatcher.process(event_id)
        await dispatcher.process(event_id)  # second call must be a no-op

        assert (await get_outcome(database, event_id)) == "notified"
        assert len(fake.sent) == 1
        assert len(await get_notifications(database, event_id)) == 1
    finally:
        await database.dispose()


async def test_queue_recovers_stale_events(tmp_path) -> None:
    settings = build_settings(tmp_path, NOTIFY_YAML)
    fake = FakeProvider(settings)
    database = Database(settings.database_url)
    await database.init()
    registry = ProviderRegistry(settings)
    registry.register(fake)
    engine = RuleEngine(load_rules(settings.rules_file))
    renderer = Renderer(settings.templates_dir)
    dispatcher = Dispatcher(
        settings=settings,
        database=database,
        registry=registry,
        engine=engine,
        renderer=renderer,
    )

    # Simulate a process that died mid-dispatch.
    event_id = await insert_event(database, state="processing")

    queue = NotificationQueue(
        dispatcher.process,
        session_factory=database.session_factory,
        concurrency=1,
        sweep_interval=0.05,
    )
    try:
        await queue.start()
        from tests.helpers import wait_until

        await wait_until(lambda: db_state_is(database, event_id, "done"))
        assert (await get_outcome(database, event_id)) == "notified"
        assert fake.sent
    finally:
        await queue.stop()
        await database.dispose()
