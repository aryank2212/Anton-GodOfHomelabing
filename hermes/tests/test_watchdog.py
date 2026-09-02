from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from app.config.settings import Settings
from app.database.models import EventRecord
from app.database.session import Database
from app.providers.registry import ProviderRegistry
from app.services.oracle import OracleClient
from app.services.watchdog import (
    Watchdog,
    action_to_command,
    parse_decision,
)
from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "app" / "templates"


class FakeQueue:
    def __init__(self) -> None:
        self.put_ids: list[str] = []

    def put(self, event_id: str) -> None:
        self.put_ids.append(event_id)

    def size(self) -> int:
        return len(self.put_ids)


class FakeBot:
    enabled = True

    def __init__(self, chat_id: int) -> None:
        self.chats = [chat_id]
        self.sent: list[tuple[int, str]] = []

    @property
    def allowed_chats(self) -> list[int]:
        return self.chats

    async def _reply(self, chat_id: int, text: str, parse_mode: str | None = None) -> None:
        self.sent.append((chat_id, text))


def settings_for(tmp_path, **overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "_env_file": None,
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'watchdog.db'}",
        "ai_enabled": True,
        "oracle_url": "http://oracle.test",
        "oracle_token": "tok",
        "watchdog_enabled": True,
        "watchdog_allowed_commands": (
            "docker restart *,docker start *,docker stop *,docker logs *"
        ),
        "watchdog_confirm_seconds": 0.05,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def mock_oracle(handler) -> OracleClient:
    settings = Settings(
        _env_file=None,
        ai_enabled=True,
        oracle_url="http://oracle.test",
        oracle_token="tok",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OracleClient(settings, database=None, client=client)  # type: ignore[arg-type]


def decide_reply(request: httpx.Request | None = None, **decision: Any) -> httpx.Response:
    payload = {
        "action": "docker_restart",
        "target": "jellyfin",
        "risk": "low",
        "reason": "crashed three times",
    }
    payload.update(decision)
    return httpx.Response(200, json={"reply": json.dumps(payload), "model": "qwen3:1.7b"})


async def fake_exists(name: str) -> bool:
    """Containers the fake daemon knows about; never touches real Docker."""
    return name in {"jellyfin", "gitea", "sentinel", "anton"}


async def insert_event(
    database: Database,
    *,
    ts: datetime,
    type_: str = "crash",
    module: str = "phoenix",
    severity: str = "error",
    metadata: dict[str, Any] | None = None,
) -> None:
    async with database.session_factory() as session:
        session.add(
            EventRecord(
                module=module,
                type=type_,
                severity=severity,
                title="Jellyfin crashed",
                message="component jellyfin failed",
                metadata_json=metadata or {"component": "jellyfin"},
                timestamp=ts,
                state="done",
            )
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Decision parsing / command rendering
# ---------------------------------------------------------------------------


def test_parse_decision_plain_json() -> None:
    decision = parse_decision(
        '{"action": "docker_restart", "target": "jellyfin", "risk": "low", "reason": "down"}'
    )
    assert decision is not None
    assert decision.action == "docker_restart"
    assert decision.target == "jellyfin"
    assert decision.risk == "low"
    assert decision.reason == "down"


def test_parse_decision_tolerates_code_fences_and_prose() -> None:
    decision = parse_decision(
        'Here is my analysis.\n```json\n{"action": "docker_start", '
        '"target": "gitea", "risk": "medium", "reason": "stopped"}\n```\nDone.'
    )
    assert decision is not None
    assert decision.action == "docker_start"
    assert decision.target == "gitea"


def test_parse_decision_unknown_action_becomes_none() -> None:
    decision = parse_decision(
        '{"action": "shutdown_host", "target": "x", "risk": "high", "reason": "y"}'
    )
    assert decision is not None
    assert decision.action == "none"


def test_parse_decision_none_action() -> None:
    decision = parse_decision('{"action": "none", "target": "", "risk": "low", "reason": "ok"}')
    assert decision is not None
    assert decision.action == "none"


def test_parse_decision_garbage_returns_none() -> None:
    assert parse_decision("") is None
    assert parse_decision("I think we should watch and wait.") is None
    assert parse_decision("not json at all") is None


def test_action_to_command_mapping() -> None:
    assert action_to_command("docker_restart", "jellyfin") == "docker restart jellyfin"
    assert action_to_command("docker_logs", "jellyfin") == "docker logs --tail 40 jellyfin"
    assert action_to_command("bogus", "jellyfin") is None


# ---------------------------------------------------------------------------
# Validation (fail-closed)
# ---------------------------------------------------------------------------


async def test_validation_rejects_disallowed_command(tmp_path) -> None:
    settings = settings_for(tmp_path, watchdog_allowed_commands="docker ps *")
    database = Database(settings.database_url)
    await database.init()
    watchdog = Watchdog(
        settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(decide_reply),
        bot=FakeBot(-100123),
    )
    try:
        decision = parse_decision(
            json.dumps(
                {"action": "docker_restart", "target": "jellyfin", "risk": "low", "reason": "x"}
            )
        )
        assert not watchdog._valid(decision, action_to_command(decision.action, decision.target))
    finally:
        await database.dispose()


async def test_validation_rejects_bad_target(tmp_path) -> None:
    settings = settings_for(tmp_path)
    database = Database(settings.database_url)
    await database.init()
    watchdog = Watchdog(
        settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(decide_reply),
        bot=FakeBot(-100123),
    )
    try:
        decision = parse_decision(
            json.dumps(
                {
                    "action": "docker_restart",
                    "target": "evil; rm -rf /",
                    "risk": "low",
                    "reason": "x",
                }
            )
        )
        assert not watchdog._valid(decision, action_to_command(decision.action, decision.target))
    finally:
        await database.dispose()


async def test_validation_fails_closed_with_empty_allowlist(tmp_path) -> None:
    settings = settings_for(tmp_path, watchdog_allowed_commands="")
    database = Database(settings.database_url)
    await database.init()
    watchdog = Watchdog(
        settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(decide_reply),
        bot=FakeBot(-100123),
    )
    try:
        assert not watchdog.enabled
        decision = parse_decision(
            json.dumps(
                {"action": "docker_restart", "target": "jellyfin", "risk": "low", "reason": "x"}
            )
        )
        assert not watchdog._valid(decision, action_to_command(decision.action, decision.target))
    finally:
        await database.dispose()


async def test_validation_accepts_allowed_command(tmp_path) -> None:
    settings = settings_for(tmp_path)
    database = Database(settings.database_url)
    await database.init()
    watchdog = Watchdog(
        settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(decide_reply),
        bot=FakeBot(-100123),
    )
    try:
        decision = parse_decision(
            json.dumps(
                {"action": "docker_restart", "target": "jellyfin", "risk": "low", "reason": "x"}
            )
        )
        assert watchdog._valid(decision, action_to_command(decision.action, decision.target))
    finally:
        await database.dispose()


# ---------------------------------------------------------------------------
# Watch loop
# ---------------------------------------------------------------------------


async def test_first_check_skips_backlog(tmp_path) -> None:
    settings = settings_for(tmp_path)
    database = Database(settings.database_url)
    await database.init()
    watchdog = Watchdog(
        settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(decide_reply),
        bot=FakeBot(-100123),
    )
    try:
        base = datetime.now(UTC)
        await insert_event(database, ts=base)
        assert await watchdog.check_once(now=base) == []
        assert watchdog._last_check == base
    finally:
        await database.dispose()


async def test_error_event_triggers_low_risk_proposal(tmp_path) -> None:
    settings = settings_for(tmp_path)
    database = Database(settings.database_url)
    await database.init()
    bot = FakeBot(-100123)
    watchdog = Watchdog(
        settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(decide_reply),
        bot=bot,
        container_exists=fake_exists,
    )
    try:
        base = datetime.now(UTC)
        await watchdog.check_once(now=base)

        await insert_event(database, ts=base + timedelta(seconds=2))
        processed = await watchdog.check_once(now=base + timedelta(seconds=5))

        assert len(processed) == 1
        assert "jellyfin" in watchdog._pending
        assert bot.sent and "Watchdog" in bot.sent[0][1]
        assert "docker restart jellyfin" in bot.sent[0][1]
        assert "3 min" not in bot.sent[0][1]  # confirm window rounded to 0 min -> shown as 1
    finally:
        await watchdog.stop()
        await database.dispose()


async def test_missing_container_skips_proposal(tmp_path) -> None:
    """A proposal for a container the daemon does not know is never surfaced."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return decide_reply(action="docker_restart", target="eloquent_wilbur")

    settings = settings_for(tmp_path, watchdog_confirm_seconds=0.05)
    database = Database(settings.database_url)
    await database.init()
    bot = FakeBot(-100123)
    watchdog = Watchdog(
        settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(handler),
        bot=bot,
        container_exists=fake_exists,
    )
    try:
        base = datetime.now(UTC)
        await watchdog.check_once(now=base)
        await insert_event(database, ts=base + timedelta(seconds=2))
        await watchdog.check_once(now=base + timedelta(seconds=5))

        assert watchdog._pending == {}
        assert bot.sent == []
        async with database.session_factory() as session:
            result = await session.execute(
                select(EventRecord).where(EventRecord.type == "watchdog.skipped")
            )
            skipped = result.scalar_one_or_none()
        assert skipped is not None
        assert "eloquent_wilbur" in skipped.title
        assert "docker_restart" in skipped.title
    finally:
        await watchdog.stop()
        await database.dispose()


async def test_auto_proceed_after_veto_window(tmp_path) -> None:
    settings = settings_for(tmp_path, watchdog_confirm_seconds=0.05)
    database = Database(settings.database_url)
    await database.init()
    bot = FakeBot(-100123)
    runs: list[str] = []

    async def fake_run(command: str, timeout: float) -> tuple[bool, str]:
        runs.append(command)
        return True, "restarted"

    watchdog = Watchdog(
        settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(decide_reply),
        bot=bot,
        run_command=fake_run,
        container_exists=fake_exists,
    )
    try:
        base = datetime.now(UTC)
        await watchdog.check_once(now=base)
        await insert_event(database, ts=base + timedelta(seconds=2))
        await watchdog.check_once(now=base + timedelta(seconds=5))

        await asyncio.sleep(0.2)

        assert runs == ["docker restart jellyfin"]
        assert "jellyfin" not in watchdog._pending
        assert any("Watchdog docker_restart jellyfin" in text for _, text in bot.sent)
        async with database.session_factory() as session:
            result = await session.execute(
                select(EventRecord).where(EventRecord.type == "watchdog.action")
            )
            assert result.scalar_one_or_none() is not None
    finally:
        await watchdog.stop()
        await database.dispose()


async def test_user_yes_executes_immediately(tmp_path) -> None:
    settings = settings_for(tmp_path)
    database = Database(settings.database_url)
    await database.init()
    bot = FakeBot(-100123)
    runs: list[str] = []

    async def fake_run(command: str, timeout: float) -> tuple[bool, str]:
        runs.append(command)
        return True, "restarted"

    watchdog = Watchdog(
        settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(decide_reply),
        bot=bot,
        run_command=fake_run,
        container_exists=fake_exists,
    )
    try:
        base = datetime.now(UTC)
        await watchdog.check_once(now=base)
        await insert_event(database, ts=base + timedelta(seconds=2))
        await watchdog.check_once(now=base + timedelta(seconds=5))

        consumed = await watchdog.handle_user_reply(-100123, "yes")
        assert consumed
        assert runs == ["docker restart jellyfin"]
        assert "jellyfin" not in watchdog._pending
    finally:
        await watchdog.stop()
        await database.dispose()


async def test_user_no_cancels_without_running(tmp_path) -> None:
    settings = settings_for(tmp_path)
    database = Database(settings.database_url)
    await database.init()
    bot = FakeBot(-100123)
    runs: list[str] = []

    async def fake_run(command: str, timeout: float) -> tuple[bool, str]:
        await asyncio.sleep(0)
        runs.append(command)
        return True, "restarted"

    watchdog = Watchdog(
        settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(decide_reply),
        bot=bot,
        run_command=fake_run,
        container_exists=fake_exists,
    )
    try:
        base = datetime.now(UTC)
        await watchdog.check_once(now=base)
        await insert_event(database, ts=base + timedelta(seconds=2))
        await watchdog.check_once(now=base + timedelta(seconds=5))

        consumed = await watchdog.handle_user_reply(-100123, "no")
        assert consumed
        assert runs == []
        assert "cancelled" in bot.sent[-1][1].lower()
        # A reply to a non-open proposal is not consumed.
        assert not await watchdog.handle_user_reply(-100123, "no")
    finally:
        await watchdog.stop()
        await database.dispose()


async def test_override_instruction_cancels_and_notes_reply(tmp_path) -> None:
    settings = settings_for(tmp_path)
    database = Database(settings.database_url)
    await database.init()
    bot = FakeBot(-100123)
    watchdog = Watchdog(
        settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(decide_reply),
        bot=bot,
        container_exists=fake_exists,
    )
    try:
        base = datetime.now(UTC)
        await watchdog.check_once(now=base)
        await insert_event(database, ts=base + timedelta(seconds=2))
        await watchdog.check_once(now=base + timedelta(seconds=5))

        consumed = await watchdog.handle_user_reply(-100123, "restart portainer instead")
        assert consumed
        assert "jellyfin" not in watchdog._pending
        assert "restart portainer instead" in bot.sent[-1][1]
    finally:
        await watchdog.stop()
        await database.dispose()


async def test_medium_risk_requires_explicit_approval(tmp_path) -> None:
    settings = settings_for(tmp_path, watchdog_confirm_seconds=0.05)
    database = Database(settings.database_url)
    await database.init()
    bot = FakeBot(-100123)
    runs: list[str] = []

    async def fake_run(command: str, timeout: float) -> tuple[bool, str]:
        runs.append(command)
        return True, "x"

    async def handler(request: httpx.Request) -> httpx.Response:
        return decide_reply(
            action="docker_stop", risk="medium", target="gitea", reason="may disrupt users"
        )

    watchdog = Watchdog(
        settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(handler),
        bot=bot,
        run_command=fake_run,
        container_exists=fake_exists,
    )
    try:
        base = datetime.now(UTC)
        await watchdog.check_once(now=base)
        await insert_event(database, ts=base + timedelta(seconds=2))
        await watchdog.check_once(now=base + timedelta(seconds=5))

        # Medium risk: no auto timer, so the window elapsing does nothing.
        await asyncio.sleep(0.15)
        assert runs == []
        assert "gitea" in watchdog._pending
        assert "needs your approval" in bot.sent[0][1]

        # Explicit operator approval runs it.
        consumed = await watchdog.handle_user_reply(-100123, "yes")
        assert consumed
        assert runs == ["docker stop gitea"]
    finally:
        await watchdog.stop()
        await database.dispose()


async def test_risk_is_deterministic_by_action(tmp_path) -> None:
    settings = settings_for(tmp_path, watchdog_confirm_seconds=0.05)
    database = Database(settings.database_url)
    await database.init()

    # restart is pinned low locally even when the model claims high risk.
    bot = FakeBot(-100123)
    runs: list[str] = []

    async def fake_run(command: str, timeout: float) -> tuple[bool, str]:
        runs.append(command)
        return True, "x"

    async def handler(request: httpx.Request) -> httpx.Response:
        return decide_reply(action="docker_restart", risk="high", target="jellyfin")

    watchdog = Watchdog(
        settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(handler),
        bot=bot,
        run_command=fake_run,
        container_exists=fake_exists,
    )
    try:
        base = datetime.now(UTC)
        await watchdog.check_once(now=base)
        await insert_event(database, ts=base + timedelta(seconds=2))
        await watchdog.check_once(now=base + timedelta(seconds=5))

        assert "jellyfin" in watchdog._pending
        assert watchdog._pending["jellyfin"].risk == "low"
        assert watchdog._pending["jellyfin"].risk_model == "high"
        assert "risk: low" in bot.sent[0][1].lower()
        assert "will run it in 1 min" in bot.sent[0][1]

        await asyncio.sleep(0.15)
        assert runs == ["docker restart jellyfin"]
    finally:
        await watchdog.stop()
        await database.dispose()

    # stop is pinned medium locally even when the model claims low risk.
    database2 = Database(settings.database_url)
    await database2.init()
    bot2 = FakeBot(-100123)
    runs2: list[str] = []

    async def fake_run2(command: str, timeout: float) -> tuple[bool, str]:
        runs2.append(command)
        return True, "x"

    async def handler2(request: httpx.Request) -> httpx.Response:
        return decide_reply(action="docker_stop", risk="low", target="gitea")

    watchdog2 = Watchdog(
        settings,
        database=database2,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(handler2),
        bot=bot2,
        run_command=fake_run2,
        container_exists=fake_exists,
    )
    try:
        base = datetime.now(UTC)
        await watchdog2.check_once(now=base)
        await insert_event(database2, ts=base + timedelta(seconds=2))
        await watchdog2.check_once(now=base + timedelta(seconds=5))

        assert "gitea" in watchdog2._pending
        assert watchdog2._pending["gitea"].risk == "medium"
        assert watchdog2._pending["gitea"].risk_model == "low"
        assert "needs your approval" in bot2.sent[0][1]

        await asyncio.sleep(0.15)
        assert runs2 == []
        await watchdog2.handle_user_reply(-100123, "yes")
        assert runs2 == ["docker stop gitea"]
    finally:
        await watchdog2.stop()
        await database2.dispose()


async def test_phoenix_escalation_requires_approval_even_for_low_risk_action(
    tmp_path,
) -> None:
    settings = settings_for(tmp_path, watchdog_confirm_seconds=0.05)
    database = Database(settings.database_url)
    await database.init()
    bot = FakeBot(-100123)
    runs: list[str] = []

    async def fake_run(command: str, timeout: float) -> tuple[bool, str]:
        runs.append(command)
        return True, "x"

    watchdog = Watchdog(
        settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(decide_reply),
        bot=bot,
        run_command=fake_run,
        container_exists=fake_exists,
    )
    try:
        base = datetime.now(UTC)
        await watchdog.check_once(now=base)
        await insert_event(
            database,
            ts=base + timedelta(seconds=2),
            type_="recovery_escalated",
            metadata={
                "component": "jellyfin",
                "strategy": "docker_restart",
                "attempts": 3,
            },
        )
        await watchdog.check_once(now=base + timedelta(seconds=5))

        # docker_restart is locally low-risk, but Phoenix already exhausted it
        # on this component: the proposal must require operator approval.
        assert "jellyfin" in watchdog._pending
        assert watchdog._pending["jellyfin"].risk == "medium"
        assert "already tried docker_restart" in bot.sent[0][1]
        assert "needs your approval" in bot.sent[0][1]

        # The veto window elapses but nothing auto-runs.
        await asyncio.sleep(0.15)
        assert runs == []
        await watchdog.handle_user_reply(-100123, "yes")
        assert runs == ["docker restart jellyfin"]
    finally:
        await watchdog.stop()
        await database.dispose()


async def test_phoenix_non_escalation_still_auto_runs(tmp_path) -> None:
    settings = settings_for(tmp_path, watchdog_confirm_seconds=0.05)
    database = Database(settings.database_url)
    await database.init()
    bot = FakeBot(-100123)
    runs: list[str] = []

    async def fake_run(command: str, timeout: float) -> tuple[bool, str]:
        runs.append(command)
        return True, "x"

    watchdog = Watchdog(
        settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(decide_reply),
        bot=bot,
        run_command=fake_run,
        container_exists=fake_exists,
    )
    try:
        base = datetime.now(UTC)
        await watchdog.check_once(now=base)
        await insert_event(
            database,
            ts=base + timedelta(seconds=2),
            type_="recovery_escalated",
            metadata={"component": "jellyfin", "strategy": "noop", "attempts": 2},
        )
        await watchdog.check_once(now=base + timedelta(seconds=5))

        # Phoenix escalated with a different strategy (noop) — watchdog's
        # docker_restart is a new attempt, so it auto-runs after the window.
        assert "jellyfin" in watchdog._pending
        assert watchdog._pending["jellyfin"].risk == "low"
        await asyncio.sleep(0.15)
        assert runs == ["docker restart jellyfin"]
    finally:
        await watchdog.stop()
        await database.dispose()


async def test_crashloop_escalates_to_approval(tmp_path) -> None:
    settings = settings_for(
        tmp_path,
        watchdog_confirm_seconds=0.05,
        watchdog_crashloop_threshold=2,
    )
    database = Database(settings.database_url)
    await database.init()
    bot = FakeBot(-100123)
    runs: list[str] = []

    async def fake_run(command: str, timeout: float) -> tuple[bool, str]:
        runs.append(command)
        return True, "x"

    watchdog = Watchdog(
        settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(decide_reply),
        bot=bot,
        run_command=fake_run,
        container_exists=fake_exists,
    )
    try:
        base = datetime.now(UTC)
        # The target already had two watchdog actions inside the window.
        watchdog._action_history["jellyfin"] = [
            base - timedelta(seconds=10),
            base - timedelta(seconds=5),
        ]
        await watchdog.check_once(now=base)
        await insert_event(
            database,
            ts=base + timedelta(seconds=2),
            module="watchyourlan",
            type_="crash",
        )
        await watchdog.check_once(now=base + timedelta(seconds=5))

        # docker_restart is locally low-risk, but the target is crash-looping:
        # further proposals require operator approval.
        assert "jellyfin" in watchdog._pending
        assert watchdog._pending["jellyfin"].risk == "medium"
        assert "crash-looping" in bot.sent[0][1]
        assert "needs your approval" in bot.sent[0][1]

        # The veto window elapses but nothing auto-runs.
        await asyncio.sleep(0.15)
        assert runs == []
        await watchdog.handle_user_reply(-100123, "yes")
        assert runs == ["docker restart jellyfin"]
    finally:
        await watchdog.stop()
        await database.dispose()


async def test_crashloop_below_threshold_still_auto_runs(tmp_path) -> None:
    settings = settings_for(
        tmp_path,
        watchdog_confirm_seconds=0.05,
        watchdog_crashloop_threshold=2,
    )
    database = Database(settings.database_url)
    await database.init()
    bot = FakeBot(-100123)
    runs: list[str] = []

    async def fake_run(command: str, timeout: float) -> tuple[bool, str]:
        runs.append(command)
        return True, "x"

    watchdog = Watchdog(
        settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(decide_reply),
        bot=bot,
        run_command=fake_run,
        container_exists=fake_exists,
    )
    try:
        base = datetime.now(UTC)
        # Only one prior action: below the threshold, so this stays low-risk.
        watchdog._action_history["jellyfin"] = [base - timedelta(seconds=10)]
        await watchdog.check_once(now=base)
        await insert_event(
            database,
            ts=base + timedelta(seconds=2),
            module="watchyourlan",
            type_="crash",
        )
        await watchdog.check_once(now=base + timedelta(seconds=5))

        assert "jellyfin" in watchdog._pending
        assert watchdog._pending["jellyfin"].risk == "low"
        await asyncio.sleep(0.15)
        assert runs == ["docker restart jellyfin"]
        # The executed action was recorded for future crash-loop detection.
        assert len(watchdog._action_history["jellyfin"]) == 2
    finally:
        await watchdog.stop()
        await database.dispose()


async def test_no_action_decision_skips(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return decide_reply(action="none", target="", reason="nothing to do")

    settings = settings_for(tmp_path)
    database = Database(settings.database_url)
    await database.init()
    bot = FakeBot(-100123)
    watchdog = Watchdog(
        settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(handler),
        bot=bot,
    )
    try:
        base = datetime.now(UTC)
        await watchdog.check_once(now=base)
        await insert_event(database, ts=base + timedelta(seconds=2))
        processed = await watchdog.check_once(now=base + timedelta(seconds=5))

        assert len(processed) == 1
        assert bot.sent == []
        assert watchdog._pending == {}
    finally:
        await watchdog.stop()
        await database.dispose()


async def test_cooldown_prevents_duplicate_proposals(tmp_path) -> None:
    settings = settings_for(tmp_path, watchdog_target_cooldown_seconds=60)
    database = Database(settings.database_url)
    await database.init()
    bot = FakeBot(-100123)
    watchdog = Watchdog(
        settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(decide_reply),
        bot=bot,
        container_exists=fake_exists,
    )
    try:
        base = datetime.now(UTC)
        await watchdog.check_once(now=base)

        await insert_event(database, ts=base + timedelta(seconds=2))
        await watchdog.check_once(now=base + timedelta(seconds=5))
        assert len(bot.sent) == 1

        # A second failure for the same target: pending entry still exists.
        await insert_event(database, ts=base + timedelta(seconds=8))
        await watchdog.check_once(now=base + timedelta(seconds=10))
        assert len(bot.sent) == 1
    finally:
        await watchdog.stop()
        await database.dispose()


async def test_status_reports_state(tmp_path) -> None:
    settings = settings_for(tmp_path)
    database = Database(settings.database_url)
    await database.init()
    watchdog = Watchdog(
        settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        oracle=mock_oracle(decide_reply),
        bot=FakeBot(-100123),
    )
    try:
        status = watchdog.status()
        assert status["enabled"] is True
        assert status["pending"] == []
    finally:
        await database.dispose()


def update(chat_id: int, text: str) -> dict[str, Any]:
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


async def test_bot_routes_reply_to_watchdog_not_oracle(tmp_path) -> None:
    """A plain-text reply to an open proposal is consumed by the watchdog."""
    from app.bot.telegram_bot import TelegramBot

    settings = settings_for(
        tmp_path,
        bot_enabled=True,
        telegram_bot_token="123:secret",
        telegram_chat_id="-100123",
    )
    database = Database(settings.database_url)
    await database.init()
    queue = FakeQueue()
    runs: list[str] = []

    async def fake_run(command: str, timeout: float) -> tuple[bool, str]:
        runs.append(command)
        return True, "restarted"

    oracle_calls: list[dict[str, Any]] = []

    def oracle_handler(request: httpx.Request) -> httpx.Response:
        oracle_calls.append(json.loads(request.content))
        if request.url.path.endswith("/v1/decide"):
            return decide_reply()
        return httpx.Response(200, json={"reply": "unexpected", "model": "qwen3:1.7b"})

    telegram_calls: list[dict[str, Any]] = []

    def telegram_handler(request: httpx.Request) -> httpx.Response:
        telegram_calls.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    oracle = OracleClient(
        settings, database, client=httpx.AsyncClient(transport=httpx.MockTransport(oracle_handler))
    )
    bot = TelegramBot(
        settings=settings,
        database=database,
        queue=queue,
        registry=ProviderRegistry(settings),
        client=httpx.AsyncClient(transport=httpx.MockTransport(telegram_handler)),
        oracle=oracle,
    )
    watchdog = Watchdog(
        settings,
        database=database,
        queue=queue,
        registry=ProviderRegistry(settings),
        oracle=oracle,
        bot=bot,
        run_command=fake_run,
        container_exists=fake_exists,
    )
    bot.attach_watchdog(watchdog)
    try:
        base = datetime.now(UTC)
        await watchdog.check_once(now=base)
        await insert_event(database, ts=base + timedelta(seconds=2))
        await watchdog.check_once(now=base + timedelta(seconds=5))

        oracle_calls.clear()
        await bot._handle_update(update(-100123, "yes"))

        assert runs == ["docker restart jellyfin"]
        # The "yes" went to the watchdog, not to the Oracle chat endpoint.
        assert oracle_calls == []
        assert "jellyfin" not in watchdog._pending
    finally:
        await watchdog.stop()
        await database.dispose()
