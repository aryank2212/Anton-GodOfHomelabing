from __future__ import annotations

import json
from typing import Any, ClassVar

import httpx
from app.config.settings import Settings
from app.database.session import Database
from app.services.approvals import ApprovalBridge


class FakeBot:
    enabled = True
    allowed_chats: ClassVar[list[int]] = [-100123]

    def __init__(self) -> None:
        self.replies: list[tuple[int, str]] = []

    async def _reply(self, chat_id: int, text: str) -> None:
        self.replies.append((chat_id, text))


def make_bridge(tmp_path, **overrides: Any) -> tuple[ApprovalBridge, Database, FakeBot]:
    kwargs: dict[str, Any] = {
        "forge_enabled": True,
        "forge_url": "http://forge:8000",
        "forge_token": "forge-token",
    }
    kwargs.update(overrides)
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'bridge.db'}",
        **kwargs,
    )
    database = Database(settings.database_url)
    bot = FakeBot()
    bot.replies = []
    bridge = ApprovalBridge(settings, database, bot=bot)
    return bridge, database, bot


def forge_handler(store: dict[str, Any]) -> Any:
    async def handler(request: httpx.Request) -> httpx.Response:
        store["url"] = str(request.url)
        store["auth"] = request.headers.get("authorization")
        store["body"] = request.content
        return httpx.Response(200, json={"ok": True, "output": "restart gitea ok"})

    return handler


async def test_bridge_disabled_without_config(tmp_path) -> None:
    bridge, database, _ = await make_bridge_async(tmp_path, forge_enabled=False)
    try:
        assert not bridge.enabled
    finally:
        await database.dispose()


async def make_bridge_async(tmp_path, **overrides: Any):
    bridge, database, bot = make_bridge(tmp_path, **overrides)
    await database.init()
    return bridge, database, bot


async def test_request_approval_sends_telegram_and_stores(tmp_path) -> None:
    bridge, database, bot = await make_bridge_async(tmp_path)
    try:
        delivered = await bridge.request_approval("abc123", "restart gitea")
        assert delivered
        assert bot.replies and bot.replies[0][0] == -100123
        assert "abc123" in bot.replies[0][1]
        assert "yes" in bot.replies[0][1]
        assert bridge.pending[0].approval_id == "abc123"
    finally:
        await bridge.stop()
        await database.dispose()


async def test_request_approval_fails_without_chat(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'b.db'}",
        forge_enabled=True,
        forge_url="http://forge:8000",
        forge_token="t",
    )
    database = Database(settings.database_url)
    await database.init()
    bridge = ApprovalBridge(settings, database, bot=FakeBot())
    bridge._bot.allowed_chats = []
    try:
        assert await bridge.request_approval("abc", "x") is False
    finally:
        await bridge.stop()
        await database.dispose()


async def test_handle_reply_approve_resolves_to_forge(tmp_path) -> None:
    store: dict[str, Any] = {}
    bridge, database, bot = await make_bridge_async(tmp_path)
    bridge._client = httpx.AsyncClient(transport=httpx.MockTransport(forge_handler(store)))
    try:
        await bridge.request_approval("abc123", "restart gitea")
        consumed = await bridge.handle_user_reply(-100123, "yes")
        assert consumed
        assert store["url"].endswith("/v1/approvals/abc123/resolve")
        assert store["auth"] == "Bearer forge-token"
        assert json.loads(store["body"]) == {"approved": True, "by": "telegram"}
        assert any("Approved" in text for _, text in bot.replies)
        assert bridge.pending == []
    finally:
        await bridge.stop()
        await database.dispose()


async def test_handle_reply_reject_resolves_to_forge(tmp_path) -> None:
    store: dict[str, Any] = {}
    bridge, database, bot = await make_bridge_async(tmp_path)
    bridge._client = httpx.AsyncClient(transport=httpx.MockTransport(forge_handler(store)))
    try:
        await bridge.request_approval("abc123", "restart gitea")
        consumed = await bridge.handle_user_reply(-100123, "no")
        assert consumed
        assert json.loads(store["body"]) == {"approved": False, "by": "telegram"}
        assert any("Rejected" in text for _, text in bot.replies)
    finally:
        await bridge.stop()
        await database.dispose()


async def test_handle_reply_ignored_when_no_pending(tmp_path) -> None:
    bridge, database, _ = await make_bridge_async(tmp_path)
    try:
        assert await bridge.handle_user_reply(-100123, "yes") is False
        assert await bridge.handle_user_reply(-100123, "what is the weather?") is False
    finally:
        await bridge.stop()
        await database.dispose()


async def test_handle_reply_ambiguous_word_not_consumed(tmp_path) -> None:
    bridge, database, _ = await make_bridge_async(tmp_path)
    try:
        await bridge.request_approval("abc123", "restart gitea")
        assert await bridge.handle_user_reply(-100123, "maybe later") is False
        assert bridge.pending  # still open
    finally:
        await bridge.stop()
        await database.dispose()


async def test_forge_unreachable_notifies_operator(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    bridge, database, bot = await make_bridge_async(tmp_path)
    bridge._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await bridge.request_approval("abc123", "restart gitea")
        await bridge.handle_user_reply(-100123, "yes")
        assert any("Forge is unreachable" in text for _, text in bot.replies)
    finally:
        await bridge.stop()
        await database.dispose()


async def test_sweep_expires_stale_approvals(tmp_path) -> None:
    bridge, database, bot = await make_bridge_async(
        tmp_path, forge_approval_timeout=-1.0, forge_approval_sweep_interval=0.01
    )
    try:
        await bridge.request_approval("abc123", "restart gitea")
        assert bridge.pending
        await bridge._sweep()  # single pass
        assert bridge.pending == []
        assert any("expired" in text for _, text in bot.replies)
    finally:
        await bridge.stop()
        await database.dispose()


async def test_route_creates_approval(app, client, monkeypatch) -> None:
    class FakeBridge:
        enabled = True

        async def request_approval(self, approval_id: str, text: str) -> bool:
            return True

    monkeypatch.setattr(app.state, "approvals", FakeBridge())
    response = await client.post("/v1/approvals", json={"id": "abc123", "text": "restart gitea"})
    assert response.status_code == 202
    assert response.json()["ok"] is True


async def test_route_503_when_bridge_disabled(app, client, monkeypatch) -> None:
    class DisabledBridge:
        enabled = False

    monkeypatch.setattr(app.state, "approvals", DisabledBridge())
    response = await client.post("/v1/approvals", json={"id": "abc123", "text": "restart gitea"})
    assert response.status_code == 503


async def test_route_rejects_invalid_approval_id(app, client, monkeypatch) -> None:
    class FakeBridge:
        enabled = True

        async def request_approval(self, approval_id: str, text: str) -> bool:
            return True

    monkeypatch.setattr(app.state, "approvals", FakeBridge())
    response = await client.post("/v1/approvals", json={"id": "bad id with spaces!", "text": "x"})
    assert response.status_code == 422
