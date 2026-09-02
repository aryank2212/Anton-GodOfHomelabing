from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

import httpx
from app.config.settings import Settings
from app.core.renderer import Renderer
from app.database.models import EventRecord, NotificationRecord, RemediationRecord
from app.database.session import Database
from app.providers.base import BaseProvider, ProviderMessage
from app.providers.registry import ProviderRegistry
from app.rules.engine import RuleEngine
from app.rules.loader import load_rules
from app.rules.models import Remediation
from app.services.dispatcher import Dispatcher
from app.services.remediator import Remediator
from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "app" / "templates"

EVENT = {
    "id": "11111111-1111-1111-1111-111111111111",
    "module": "watcher",
    "type": "disk.usage",
    "severity": "critical",
    "title": "Disk full",
    "message": "/ is 99% full",
    "metadata": {"usage_percent": 99.0},
    "tags": ["storage"],
}

REMEDIATE_YAML = """
version: 1
rules:
  - name: "remediate_disk"
    action: remediate
    when:
      module: "watcher"
    remediation:
      kind: command
      command: "echo remediated"
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

    @property
    def enabled(self) -> bool:
        return True

    async def send(self, message: ProviderMessage) -> None:
        self.sent.append(message)


class FakeQueue:
    def __init__(self) -> None:
        self.put_ids: list[str] = []

    def put(self, event_id: str) -> None:
        self.put_ids.append(event_id)

    def size(self) -> int:
        return len(self.put_ids)


def settings_for(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


def mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Remediator unit tests
# ---------------------------------------------------------------------------


async def test_http_remediation_calls_templated_endpoint() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content)
        captured["headers"] = request.headers
        return httpx.Response(200)

    remediator = Remediator(
        settings_for(remediation_enabled=True),
        renderer=Renderer(TEMPLATES_DIR),
        client=mock_client(handler),
    )
    result = await remediator.run(
        Remediation(
            kind="http",
            method="post",
            url="https://hooks.example/{{ event.module }}",
            headers={"X-Secret": "s3cret"},
            body={"type": "{{ event.type }}", "id": "{{ event.id }}"},
        ),
        EVENT,
    )
    assert result.success
    assert captured["method"] == "POST"
    assert captured["url"] == "https://hooks.example/watcher"
    assert captured["json"] == {"type": "disk.usage", "id": EVENT["id"]}
    assert captured["headers"]["X-Secret"] == "s3cret"


async def test_http_remediation_reports_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    remediator = Remediator(
        settings_for(remediation_enabled=True),
        client=mock_client(handler),
    )
    result = await remediator.run(Remediation(kind="http", url="https://hooks.example/x"), EVENT)
    assert not result.success
    assert result.detail


async def test_command_remediation_runs_injected_runner() -> None:
    calls: list[tuple[str, float]] = []

    async def fake_run(command: str, timeout: float) -> str:
        calls.append((command, timeout))
        return "ok"

    remediator = Remediator(
        settings_for(remediation_enabled=True),
        run_command=fake_run,
    )
    result = await remediator.run(
        Remediation(kind="command", command="systemctl restart ollama"), EVENT
    )
    assert result.success
    assert result.detail == "ok"
    assert calls == [("systemctl restart ollama", 30.0)]


async def test_default_command_runner_executes_shell() -> None:
    remediator = Remediator(settings_for(remediation_enabled=True))
    result = await remediator.run(
        Remediation(kind="command", command="echo hermes-remediation-ok"), EVENT
    )
    assert result.success
    assert result.detail == "hermes-remediation-ok"


async def test_docker_restart_builds_command() -> None:
    calls: list[str] = []

    async def fake_run(command: str, timeout: float) -> str:
        calls.append(command)
        return "done"

    remediator = Remediator(
        settings_for(remediation_enabled=True),
        run_command=fake_run,
    )
    result = await remediator.run(Remediation(kind="docker_restart", container="gitea"), EVENT)
    assert result.success
    assert calls == ["docker restart gitea"]


async def test_remediation_respects_allow_list() -> None:
    async def fake_run(command: str, timeout: float) -> str:
        return "ok"

    settings = settings_for(
        remediation_enabled=True, remediation_allowed_commands="docker restart *"
    )
    remediator = Remediator(settings, run_command=fake_run)

    allowed = await remediator.run(Remediation(kind="docker_restart", container="gitea"), EVENT)
    assert allowed.success

    blocked = await remediator.run(Remediation(kind="command", command="rm -rf /"), EVENT)
    assert not blocked.success
    assert "not allowed" in blocked.detail


async def test_remediation_disabled_without_flag() -> None:
    remediator = Remediator(settings_for(remediation_enabled=False))
    result = await remediator.run(Remediation(kind="command", command="echo hi"), EVENT)
    assert not result.success
    assert "disabled" in result.detail


# ---------------------------------------------------------------------------
# Dispatcher integration
# ---------------------------------------------------------------------------


async def build_dispatcher(
    tmp_path: Path, rules_yaml: str, *, remediation_enabled: bool
) -> tuple[Dispatcher, Database, FakeProvider]:
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(rules_yaml, encoding="utf-8")
    settings = settings_for(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'hermes.db'}",
        rules_file=str(rules_file),
        templates_dir=str(TEMPLATES_DIR),
        remediation_enabled=remediation_enabled,
        notification_max_attempts=1,
    )
    database = Database(settings.database_url)
    await database.init()

    registry = ProviderRegistry(settings)
    fake = FakeProvider(settings)
    registry.register(fake)
    dispatcher = Dispatcher(
        settings=settings,
        database=database,
        registry=registry,
        engine=RuleEngine(load_rules(settings.rules_file)),
        renderer=Renderer(settings.templates_dir),
    )
    return dispatcher, database, fake


async def insert_event(database: Database) -> str:
    async with database.session_factory() as session:
        event = EventRecord(
            module="watcher",
            type="disk.usage",
            severity="error",
            title="Disk usage high",
            message="boom",
            state="pending",
        )
        session.add(event)
        await session.commit()
        return event.id


async def get_outcome(database: Database, event_id: str) -> str | None:
    async with database.session_factory() as session:
        event = await session.get(EventRecord, event_id)
    return event.outcome if event else None


async def get_notifications(database: Database, event_id: str) -> list:
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


async def get_remediations(database: Database, event_id: str) -> list:
    async with database.session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(RemediationRecord).where(RemediationRecord.event_id == event_id)
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


async def test_dispatcher_runs_remediation_and_notifies(tmp_path) -> None:
    dispatcher, database, fake = await build_dispatcher(
        tmp_path, REMEDIATE_YAML, remediation_enabled=True
    )
    try:
        event_id = await insert_event(database)
        await dispatcher.process(event_id)

        assert (await get_outcome(database, event_id)) == "remediated"
        assert fake.sent
        remediations = await get_remediations(database, event_id)
        assert len(remediations) == 1
        assert remediations[0].kind == "command"
        assert remediations[0].status == "done"
        assert remediations[0].rule == "remediate_disk"
        assert remediations[0].detail == "remediated"
    finally:
        await dispatcher.close()
        await database.dispose()


async def test_dispatcher_marks_failed_remediation(tmp_path) -> None:
    dispatcher, database, _ = await build_dispatcher(
        tmp_path, REMEDIATE_YAML, remediation_enabled=False
    )
    try:
        event_id = await insert_event(database)
        await dispatcher.process(event_id)

        assert (await get_outcome(database, event_id)) == "remediation_failed"
        remediations = await get_remediations(database, event_id)
        assert len(remediations) == 1
        assert remediations[0].status == "failed"
        assert "disabled" in remediations[0].detail
    finally:
        await dispatcher.close()
        await database.dispose()
