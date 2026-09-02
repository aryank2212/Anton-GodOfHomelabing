from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from app.bot.telegram_bot import TelegramBot
from app.config.settings import Settings
from app.database.models import ChatMessage, EventRecord
from app.database.session import Database
from app.providers.registry import ProviderRegistry
from app.services.oracle import OracleClient
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


def settings_for(tmp_path, **overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "_env_file": None,
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}",
        "bot_enabled": True,
        "telegram_bot_token": "123:secret",
        "telegram_chat_id": "-100123",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def make_bot(tmp_path, **overrides) -> tuple[TelegramBot, Database, FakeQueue]:
    settings = settings_for(tmp_path, **overrides)
    database = Database(settings.database_url)
    await database.init()
    queue = FakeQueue()
    registry = ProviderRegistry(settings)
    bot = TelegramBot(settings=settings, database=database, queue=queue, registry=registry)
    return bot, database, queue


def make_oracle(tmp_path, handler, database: Database) -> OracleClient:
    settings = settings_for(
        tmp_path,
        ai_enabled=True,
        oracle_url="http://oracle.test",
        oracle_token="tok",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OracleClient(settings, database, client=client)


async def insert_event(
    database: Database, *, module: str, type_: str, severity: str, title: str
) -> None:
    async with database.session_factory() as session:
        session.add(
            EventRecord(
                module=module,
                type=type_,
                severity=severity,
                title=title,
                state="done",
            )
        )
        await session.commit()


def update(chat_id: int, text: str) -> dict[str, Any]:
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


# ---------------------------------------------------------------------------
# Enablement / authorization
# ---------------------------------------------------------------------------


async def test_bot_disabled_without_flag(tmp_path) -> None:
    settings = settings_for(tmp_path, bot_enabled=False)
    database = Database(settings.database_url)
    await database.init()
    registry = ProviderRegistry(settings)
    bot = TelegramBot(
        settings=settings,
        database=database,
        queue=FakeQueue(),
        registry=registry,
        client=mock_client(lambda request: httpx.Response(200)),
    )
    try:
        assert not bot.enabled
    finally:
        await database.dispose()


async def test_bot_requires_telegram_credentials(tmp_path) -> None:
    settings = settings_for(tmp_path, telegram_bot_token=None)
    database = Database(settings.database_url)
    await database.init()
    bot = TelegramBot(
        settings=settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
    )
    try:
        assert not bot.enabled
    finally:
        await database.dispose()


async def test_allowed_chats_parsing(tmp_path) -> None:
    bot, database, _ = await make_bot(tmp_path, telegram_chat_id="-100123, 456, -789")
    try:
        assert bot.allowed_chats == [-100123, 456, -789]
    finally:
        await database.dispose()


async def test_unauthorized_chat_is_ignored(tmp_path) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True, "result": {}})

    bot, database, _ = await make_bot(
        tmp_path,
        telegram_chat_id="-100123",
    )
    bot._client = mock_client(handler)
    try:
        await bot._handle_update(update(999999, "/status"))
        assert captured == []
        assert database is not None
    finally:
        await database.dispose()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def test_alerts_command_lists_recent_alerts(tmp_path) -> None:
    bot, database, _ = await make_bot(tmp_path)
    try:
        await insert_event(
            database, module="watcher", type_="disk.usage", severity="error", title="Disk full"
        )
        await insert_event(
            database, module="gitea", type_="crash", severity="info", title="Started"
        )

        reply, parse_mode = await bot._dispatch("/alerts", "")
        assert parse_mode is None
        assert "Disk full" in reply
        assert "ERROR" in reply
        assert "Started" not in reply  # info events are not alerts
    finally:
        await database.dispose()


async def test_events_command_lists_all_recent(tmp_path) -> None:
    bot, database, _ = await make_bot(tmp_path)
    try:
        await insert_event(
            database, module="watcher", type_="disk.usage", severity="error", title="Disk full"
        )
        reply, _ = await bot._dispatch("/events", "1")
        assert "Disk full" in reply
    finally:
        await database.dispose()


async def test_status_command_reports_health(tmp_path) -> None:
    bot, database, _ = await make_bot(tmp_path)
    try:
        await insert_event(database, module="watcher", type_="scan", severity="error", title="x")
        reply, _ = await bot._dispatch("/status", "")
        assert "Hermes" in reply
        assert "Database: ok" in reply
        assert "Alerts (1h): 1" in reply
    finally:
        await database.dispose()


async def test_cmd_disabled_by_default(tmp_path) -> None:
    bot, database, _ = await make_bot(tmp_path)
    try:
        reply, _ = await bot._dispatch("/cmd", "ls")
        assert "disabled" in reply
    finally:
        await database.dispose()


async def test_cmd_allowed_when_flag_set(tmp_path) -> None:
    bot, database, _ = await make_bot(tmp_path, bot_allow_cmd=True)
    try:
        reply, parse_mode = await bot._dispatch("/cmd", "echo bot-ok")
        assert parse_mode == "HTML"
        assert "bot-ok" in reply
    finally:
        await database.dispose()


async def test_unknown_command_returns_help(tmp_path) -> None:
    bot, database, _ = await make_bot(tmp_path)
    try:
        reply, _ = await bot._dispatch("/bogus", "")
        assert "Hermes Telegram bot" in reply
    finally:
        await database.dispose()


# ---------------------------------------------------------------------------
# Update handling (network + event logging)
# ---------------------------------------------------------------------------


async def test_handle_update_replies_and_logs_command(tmp_path) -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    bot, database, queue = await make_bot(tmp_path)
    bot._client = mock_client(handler)
    try:
        await bot._handle_update(update(-100123, "/providers"))

        assert captured and captured[0]["chat_id"] == -100123
        assert "providers" in captured[0]["text"]

        # The command is recorded as a hermes/bot.command event.
        async with database.session_factory() as session:
            rows = (
                (await session.execute(select(EventRecord).where(EventRecord.module == "hermes")))
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].type == "bot.command"
        assert rows[0].metadata_json["command"] == "/providers"
        assert queue.put_ids == [rows[0].id]
    finally:
        await database.dispose()


async def test_rate_limit_replies_throttled(tmp_path) -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    bot, database, _ = await make_bot(tmp_path, bot_rate_limit_per_minute=1)
    bot._client = mock_client(handler)
    try:
        await bot._handle_update(update(-100123, "/status"))
        await bot._handle_update(update(-100123, "/status"))

        assert len(captured) == 2
        assert "Too many commands" in captured[1]["text"]
    finally:
        await database.dispose()


# ---------------------------------------------------------------------------
# AI (Oracle) integration
# ---------------------------------------------------------------------------


async def test_plain_text_asks_oracle_and_replies(tmp_path) -> None:
    telegram: list[dict[str, Any]] = []
    oracle_calls: list[dict[str, Any]] = []

    def telegram_handler(request: httpx.Request) -> httpx.Response:
        telegram.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    def oracle_handler(request: httpx.Request) -> httpx.Response:
        oracle_calls.append(json.loads(request.content))
        return httpx.Response(200, json={"reply": "42", "model": "qwen3:1.7b"})

    settings = settings_for(
        tmp_path,
        ai_enabled=True,
        oracle_url="http://oracle.test",
        oracle_token="tok",
    )
    database = Database(settings.database_url)
    await database.init()
    oracle = OracleClient(
        settings, database, client=httpx.AsyncClient(transport=httpx.MockTransport(oracle_handler))
    )
    bot = TelegramBot(
        settings=settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        client=mock_client(telegram_handler),
        oracle=oracle,
    )
    try:
        await bot._handle_update(update(-100123, "what is the meaning of life"))

        assert oracle_calls and oracle_calls[0]["message"] == "what is the meaning of life"
        assert oracle_calls[0]["history"] == []
        assert telegram and "42" in telegram[0]["text"]

        # The exchange is logged as a hermes/bot.ask event.
        async with database.session_factory() as session:
            rows = (
                (await session.execute(select(EventRecord).where(EventRecord.type == "bot.ask")))
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].severity == "info"
        assert rows[0].metadata_json["model"] == "qwen3:1.7b"
    finally:
        await database.dispose()


async def test_plain_text_sends_history_on_followup(tmp_path) -> None:
    oracle_calls: list[dict[str, Any]] = []

    def oracle_handler(request: httpx.Request) -> httpx.Response:
        oracle_calls.append(json.loads(request.content))
        return httpx.Response(200, json={"reply": "answer", "model": "qwen3:1.7b"})

    settings = settings_for(
        tmp_path,
        ai_enabled=True,
        oracle_url="http://oracle.test",
        oracle_token="tok",
    )
    database = Database(settings.database_url)
    await database.init()
    oracle = OracleClient(
        settings, database, client=httpx.AsyncClient(transport=httpx.MockTransport(oracle_handler))
    )
    bot = TelegramBot(
        settings=settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        client=mock_client(lambda request: httpx.Response(200, json={"ok": True})),
        oracle=oracle,
    )
    try:
        await bot._handle_update(update(-100123, "first question"))
        await bot._handle_update(update(-100123, "second question"))

        assert len(oracle_calls) == 2
        assert oracle_calls[1]["history"] == [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "answer"},
        ]
    finally:
        await database.dispose()


async def test_plain_text_replies_warning_when_oracle_down(tmp_path) -> None:
    telegram: list[dict[str, Any]] = []

    def telegram_handler(request: httpx.Request) -> httpx.Response:
        telegram.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    def oracle_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"detail": "ollama request failed: boom"})

    settings = settings_for(
        tmp_path,
        ai_enabled=True,
        oracle_url="http://oracle.test",
        oracle_token="tok",
    )
    database = Database(settings.database_url)
    await database.init()
    oracle = OracleClient(
        settings, database, client=httpx.AsyncClient(transport=httpx.MockTransport(oracle_handler))
    )
    bot = TelegramBot(
        settings=settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        client=mock_client(telegram_handler),
        oracle=oracle,
    )
    try:
        await bot._handle_update(update(-100123, "hello?"))
        assert telegram and "Oracle unavailable" in telegram[0]["text"]

        async with database.session_factory() as session:
            rows = (
                (await session.execute(select(EventRecord).where(EventRecord.type == "bot.ask")))
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].severity == "warning"
    finally:
        await database.dispose()


async def test_plain_text_ignored_when_ai_disabled(tmp_path) -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    bot, database, _ = await make_bot(tmp_path)
    bot._client = mock_client(handler)
    try:
        await bot._handle_update(update(-100123, "this should be ignored"))
        assert captured == []
    finally:
        await database.dispose()


async def test_plain_text_sends_live_context_to_oracle(tmp_path) -> None:
    oracle_calls: list[dict[str, Any]] = []

    def oracle_handler(request: httpx.Request) -> httpx.Response:
        oracle_calls.append(json.loads(request.content))
        return httpx.Response(200, json={"reply": "answer", "model": "qwen3:1.7b"})

    settings = settings_for(
        tmp_path,
        ai_enabled=True,
        oracle_url="http://oracle.test",
        oracle_token="tok",
    )
    database = Database(settings.database_url)
    await database.init()
    await insert_event(
        database, module="watcher", type_="disk.usage", severity="error", title="Disk full"
    )
    oracle = OracleClient(
        settings, database, client=httpx.AsyncClient(transport=httpx.MockTransport(oracle_handler))
    )
    bot = TelegramBot(
        settings=settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        client=mock_client(lambda request: httpx.Response(200, json={"ok": True})),
        oracle=oracle,
    )
    try:
        await bot._handle_update(update(-100123, "what is going on?"))
        assert oracle_calls and "context" in oracle_calls[0]
        context = oracle_calls[0]["context"]
        assert "Hermes live state" in context
        assert "Disk full" in context
    finally:
        await database.dispose()


async def test_plain_text_omits_context_when_disabled(tmp_path) -> None:
    oracle_calls: list[dict[str, Any]] = []

    def oracle_handler(request: httpx.Request) -> httpx.Response:
        oracle_calls.append(json.loads(request.content))
        return httpx.Response(200, json={"reply": "answer", "model": "qwen3:1.7b"})

    settings = settings_for(
        tmp_path,
        ai_enabled=True,
        ai_context_enabled=False,
        oracle_url="http://oracle.test",
        oracle_token="tok",
    )
    database = Database(settings.database_url)
    await database.init()
    oracle = OracleClient(
        settings, database, client=httpx.AsyncClient(transport=httpx.MockTransport(oracle_handler))
    )
    bot = TelegramBot(
        settings=settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        client=mock_client(lambda request: httpx.Response(200, json={"ok": True})),
        oracle=oracle,
    )
    try:
        await bot._handle_update(update(-100123, "hello?"))
        assert oracle_calls and "context" not in oracle_calls[0]
    finally:
        await database.dispose()


async def test_ai_history_is_persisted_in_database(tmp_path) -> None:
    def oracle_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"reply": "answer", "model": "qwen3:1.7b"})

    settings = settings_for(
        tmp_path,
        ai_enabled=True,
        oracle_url="http://oracle.test",
        oracle_token="tok",
    )
    database = Database(settings.database_url)
    await database.init()
    oracle = OracleClient(
        settings, database, client=httpx.AsyncClient(transport=httpx.MockTransport(oracle_handler))
    )
    bot = TelegramBot(
        settings=settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        client=mock_client(lambda request: httpx.Response(200, json={"ok": True})),
        oracle=oracle,
    )
    try:
        await bot._handle_update(update(-100123, "first question"))

        async with database.session_factory() as session:
            rows = (
                (await session.execute(select(ChatMessage).order_by(ChatMessage.created_at)))
                .scalars()
                .all()
            )
        assert [(row.chat_id, row.role, row.content) for row in rows] == [
            (-100123, "user", "first question"),
            (-100123, "assistant", "answer"),
        ]

        # A fresh client still has the history (persistence, not memory).
        reloaded = OracleClient(
            settings,
            database,
            client=httpx.AsyncClient(transport=httpx.MockTransport(oracle_handler)),
        )
        assert await reloaded.history(-100123) == [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "answer"},
        ]
    finally:
        await database.dispose()


async def test_plain_text_routes_to_agent_loop(tmp_path) -> None:
    telegram: list[dict[str, Any]] = []
    oracle_calls: list[tuple[str, dict[str, Any]]] = []

    def telegram_handler(request: httpx.Request) -> httpx.Response:
        telegram.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    def oracle_handler(request: httpx.Request) -> httpx.Response:
        oracle_calls.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "reply": "uptime-kuma is running.",
                "model": "qwen3:8b",
                "steps": 1,
                "tools": [{"tool": "docker_ps", "decision": "allowed", "ok": True}],
            },
        )

    settings = settings_for(
        tmp_path,
        ai_enabled=True,
        oracle_url="http://oracle.test",
        oracle_token="tok",
    )
    database = Database(settings.database_url)
    await database.init()
    oracle = OracleClient(
        settings, database, client=httpx.AsyncClient(transport=httpx.MockTransport(oracle_handler))
    )
    bot = TelegramBot(
        settings=settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        client=mock_client(telegram_handler),
        oracle=oracle,
    )
    try:
        await bot._handle_update(update(-100123, "is uptime-kuma up?"))
        path, payload = oracle_calls[0]
        assert path == "/v1/agent"
        assert payload["message"] == "is uptime-kuma up?"
        assert "history" in payload
        text = telegram[0]["text"]
        assert "uptime-kuma is running." in text
        assert "docker_ps" in text  # tool-call note
        assert "qwen3:8b" in text
    finally:
        await database.dispose()


async def test_agent_falls_back_to_ask_when_gateway_lacks_endpoint(tmp_path) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    telegram: list[dict[str, Any]] = []

    def telegram_handler(request: httpx.Request) -> httpx.Response:
        telegram.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    def oracle_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((path, json.loads(request.content)))
        if path == "/v1/agent":
            return httpx.Response(404, json={"detail": "Not Found"})
        return httpx.Response(200, json={"reply": "chat answer", "model": "qwen3:1.7b"})

    settings = settings_for(
        tmp_path,
        ai_enabled=True,
        oracle_url="http://oracle.test",
        oracle_token="tok",
    )
    database = Database(settings.database_url)
    await database.init()
    oracle = OracleClient(
        settings, database, client=httpx.AsyncClient(transport=httpx.MockTransport(oracle_handler))
    )
    bot = TelegramBot(
        settings=settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        client=mock_client(telegram_handler),
        oracle=oracle,
    )
    try:
        await bot._handle_update(update(-100123, "hi"))
        assert [path for path, _ in calls] == ["/v1/agent", "/v1/ask"]
        assert "chat answer" in telegram[0]["text"]
        assert "tool calls" not in telegram[0]["text"]
    finally:
        await database.dispose()


async def test_agent_uses_ask_when_opt_out(tmp_path) -> None:
    calls: list[str] = []

    def oracle_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"reply": "answer", "model": "qwen3:1.7b"})

    settings = settings_for(
        tmp_path,
        ai_enabled=True,
        ai_use_agent=False,
        oracle_url="http://oracle.test",
        oracle_token="tok",
    )
    database = Database(settings.database_url)
    await database.init()
    oracle = OracleClient(
        settings, database, client=httpx.AsyncClient(transport=httpx.MockTransport(oracle_handler))
    )
    bot = TelegramBot(
        settings=settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        client=mock_client(lambda request: httpx.Response(200, json={"ok": True})),
        oracle=oracle,
    )
    try:
        await bot._handle_update(update(-100123, "hello"))
        assert calls == ["/v1/ask"]
    finally:
        await database.dispose()
    bot, database, _ = await make_bot(
        tmp_path, bot_allow_cmd=True, bot_allowed_commands="ls *, echo *, df *"
    )
    try:
        allowed, _ = await bot._dispatch("/cmd", "echo bot-ok")
        assert "bot-ok" in allowed

        blocked, _ = await bot._dispatch("/cmd", "rm -rf /tmp")
        assert "not allowed" in blocked
    finally:
        await database.dispose()


# ---------------------------------------------------------------------------
# Dots command (Argus internet investigations)
# ---------------------------------------------------------------------------


async def make_dots_bot(tmp_path, handler) -> tuple[TelegramBot, Database]:
    settings = settings_for(tmp_path, bot_argus_url="http://argus.test")
    database = Database(settings.database_url)
    await database.init()
    bot = TelegramBot(
        settings=settings,
        database=database,
        queue=FakeQueue(),
        registry=ProviderRegistry(settings),
        client=mock_client(handler),
    )
    return bot, database


def dots_run(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "dot_run_id": "1a2b3c4d-5e6f-4a5b-8c9d-0e1f2a3b4c5d",
        "topic": "llama.cpp",
        "status": "queued",
        "iterations_target": 12,
        "iterations_done": 0,
        "dots_kept": 0,
        "evidence_count": 0,
        "summary": "",
        "error": None,
        "created_at": "2026-08-29T03:00:00+00:00",
    }
    row.update(overrides)
    return row


async def test_dots_start_queues_run(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        assert request.url.path == "/v1/dots"
        assert body["topic"] == "llama.cpp local LLM inference"
        assert body["iterations"] == 6
        return httpx.Response(
            202,
            json={
                "dot_run_id": "1a2b3c4d-5e6f-4a5b-8c9d-0e1f2a3b4c5d",
                "topic": body["topic"],
                "status": "queued",
                "iterations_target": 6,
            },
        )

    bot, database = await make_dots_bot(tmp_path, handler)
    try:
        reply, _ = await bot._dispatch("/dots", "llama.cpp local LLM inference --i=6")
        assert "🚀 Dots run started: llama.cpp local LLM inference" in reply
        assert "1a2b3c4d-5e6f" in reply
    finally:
        await database.dispose()


async def test_dots_start_default_iterations_when_flag_omitted(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        assert "iterations" not in body
        return httpx.Response(202, json={**dots_run(), "topic": body["topic"]})

    bot, database = await make_dots_bot(tmp_path, handler)
    try:
        reply, _ = await bot._dispatch("/dots", "quantum computing")
        assert "Dots run started: quantum computing" in reply
    finally:
        await database.dispose()


async def test_dots_iterations_range_enforced(tmp_path) -> None:
    bot, database = await make_dots_bot(tmp_path, lambda _: httpx.Response(500))
    try:
        reply, _ = await bot._dispatch("/dots", "topic --i=99")
        assert "iterations must be 1..30" in reply
    finally:
        await database.dispose()


async def test_dots_refused_returns_detail(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "dots engine is disabled"})

    bot, database = await make_dots_bot(tmp_path, handler)
    try:
        reply, _ = await bot._dispatch("/dots", "topic")
        assert "dots engine is disabled" in reply
    finally:
        await database.dispose()


async def test_dots_list_recent_runs(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/dots"
        assert request.url.params["limit"] == "5"
        return httpx.Response(
            200,
            json={
                "items": [
                    dots_run(status="running", iterations_done=7, dots_kept=9, evidence_count=12),
                    dots_run(
                        status="completed",
                        dot_run_id="9f8e7d6c-5b4a-4b3c-8d2e-1f0a9b8c7d6e",
                        topic="Ollama",
                        iterations_done=2,
                    ),
                ],
                "total": 2,
            },
        )

    bot, database = await make_dots_bot(tmp_path, handler)
    try:
        reply, _ = await bot._dispatch("/dots", "")
        assert "Recent dot runs" in reply
        assert "llama.cpp" in reply
        assert "9 dots, 12 ev" in reply
        assert "Ollama" in reply
    finally:
        await database.dispose()


async def test_dots_list_empty(tmp_path) -> None:
    bot, database = await make_dots_bot(
        tmp_path,
        lambda _: httpx.Response(200, json={"items": [], "total": 0}),
    )
    try:
        reply, _ = await bot._dispatch("/dots", "")
        assert "No dot runs yet" in reply
    finally:
        await database.dispose()


async def test_dots_run_completed_shows_summary(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/dots/1a2b3c4d-5e6f-4a5b-8c9d-0e1f2a3b4c5d"
        return httpx.Response(
            200,
            json=dots_run(
                status="completed",
                iterations_done=2,
                dots_kept=6,
                evidence_count=10,
                summary="llama.cpp keeps improving CPU inference on consumer hardware.",
            ),
        )

    bot, database = await make_dots_bot(tmp_path, handler)
    try:
        reply, _ = await bot._dispatch(
            "/dots", "1a2b3c4d-5e6f-4a5b-8c9d-0e1f2a3b4c5d"
        )
        assert "✅" in reply
        assert "6 dots, 10 ev" in reply
        assert "keeps improving CPU inference" in reply
    finally:
        await database.dispose()


async def test_dots_run_not_found(tmp_path) -> None:
    bot, database = await make_dots_bot(
        tmp_path, lambda _: httpx.Response(404, json={"detail": "dot run not found"})
    )
    try:
        reply, _ = await bot._dispatch(
            "/dots", "1a2b3c4d-5e6f-4a5b-8c9d-0e1f2a3b4c5d"
        )
        assert "No dot run" in reply
    finally:
        await database.dispose()


async def test_dots_unreachable(tmp_path) -> None:
    bot, database = await make_dots_bot(
        tmp_path, lambda _: (_ for _ in ()).throw(httpx.ConnectError("boom"))
    )
    try:
        reply, _ = await bot._dispatch("/dots", "topic")
        assert "Argus unreachable" in reply
    finally:
        await database.dispose()
