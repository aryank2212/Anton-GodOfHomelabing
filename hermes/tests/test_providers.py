from __future__ import annotations

import json
from typing import Any, ClassVar

import httpx
import pytest
from app.config.settings import Settings
from app.providers.base import ProviderError, ProviderMessage
from app.providers.discord import DiscordProvider
from app.providers.email import EmailProvider
from app.providers.ntfy import NtfyProvider
from app.providers.telegram import TelegramProvider
from app.providers.webhook import WebhookProvider

EVENT = {
    "id": "11111111-1111-1111-1111-111111111111",
    "timestamp": "2026-01-01T00:00:00+00:00",
    "module": "watcher",
    "type": "disk.usage",
    "severity": "error",
    "title": "Disk usage high",
    "message": "/data is at 85%",
    "metadata": {"usage_percent": 85.0},
    "tags": ["storage"],
    "correlation_id": None,
}


def settings_for(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


def message(rendered: dict[str, str] | None = None, event: dict | None = None) -> ProviderMessage:
    return ProviderMessage(
        event_id=EVENT["id"], event=event or EVENT, rendered=rendered or {"text": "hello"}
    )


def mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------


async def test_discord_sends_content() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content)
        return httpx.Response(204)

    provider = DiscordProvider(
        settings_for(discord_webhook_url="https://discord.example/hook"),
        client=mock_client(handler),
    )
    await provider.send(message({"text": "disk full"}))
    assert captured["url"] == "https://discord.example/hook"
    assert captured["json"]["content"] == "disk full"


async def test_discord_raises_on_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    provider = DiscordProvider(
        settings_for(discord_webhook_url="https://discord.example/hook"),
        client=mock_client(handler),
    )
    with pytest.raises(ProviderError):
        await provider.send(message())


def test_discord_disabled_without_url() -> None:
    provider = DiscordProvider(settings_for())
    assert not provider.enabled


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


async def test_telegram_sends_to_chat() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {}})

    provider = TelegramProvider(
        settings_for(
            telegram_bot_token="123:secret",
            telegram_chat_id="-100123",
        ),
        client=mock_client(handler),
    )
    await provider.send(message({"text": "disk full"}))
    assert captured["url"] == "https://api.telegram.org/bot123:secret/sendMessage"
    assert captured["json"]["chat_id"] == "-100123"
    assert captured["json"]["text"] == "disk full"


async def test_telegram_raises_on_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": "chat not found"})

    provider = TelegramProvider(
        settings_for(telegram_bot_token="t", telegram_chat_id="c"),
        client=mock_client(handler),
    )
    with pytest.raises(ProviderError):
        await provider.send(message())


def test_telegram_disabled_without_credentials() -> None:
    assert not TelegramProvider(settings_for()).enabled
    assert not TelegramProvider(settings_for(telegram_bot_token="t")).enabled


# ---------------------------------------------------------------------------
# ntfy
# ---------------------------------------------------------------------------


async def test_ntfy_publishes_with_severity_priority() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content)
        captured["headers"] = request.headers
        return httpx.Response(200, json={"id": "x"})

    provider = NtfyProvider(
        settings_for(ntfy_url="https://ntfy.example", ntfy_topic="anton"),
        client=mock_client(handler),
    )
    await provider.send(message({"text": "disk full"}))
    assert captured["url"] == "https://ntfy.example/anton"
    assert captured["json"]["priority"] == 4  # error
    assert captured["json"]["title"] == "Disk usage high"


async def test_ntfy_maps_critical_priority() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "x"})

    provider = NtfyProvider(
        settings_for(ntfy_topic="anton"),
        client=mock_client(handler),
    )
    critical = {**EVENT, "severity": "critical"}
    await provider.send(message(event=critical))
    assert captured["json"]["priority"] == 5


async def test_ntfy_sends_auth_header() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(200, json={"id": "x"})

    provider = NtfyProvider(
        settings_for(ntfy_topic="anton", ntfy_token="s3cret"),
        client=mock_client(handler),
    )
    await provider.send(message())
    assert captured["headers"]["Authorization"] == "Bearer s3cret"


# ---------------------------------------------------------------------------
# Generic webhook
# ---------------------------------------------------------------------------


async def test_webhook_posts_rendered_payload() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        return httpx.Response(200)

    provider = WebhookProvider(
        settings_for(webhook_url="https://hooks.example/anton"),
        client=mock_client(handler),
    )
    await provider.send(message({"payload": '{"hello": "world", "source": "hermes"}'}))
    assert captured["json"] == {"hello": "world", "source": "hermes"}


async def test_webhook_raises_on_invalid_payload() -> None:
    provider = WebhookProvider(
        settings_for(webhook_url="https://hooks.example/anton"),
        client=mock_client(lambda request: httpx.Response(200)),
    )
    with pytest.raises(ProviderError):
        await provider.send(message({"payload": "not json"}))


# ---------------------------------------------------------------------------
# Email (SMTP)
# ---------------------------------------------------------------------------


class FakeSMTP:
    instances: ClassVar[list[FakeSMTP]] = []

    def __init__(self, host: str, port: int, timeout: int | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.login_credentials: tuple[str, str] | None = None
        self.sent: list[str] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.login_credentials = (username, password)

    def send_message(self, email) -> None:
        self.sent.append(email)


async def test_email_sends_via_smtp(monkeypatch) -> None:
    monkeypatch.setattr("app.providers.email.smtplib.SMTP", FakeSMTP)
    FakeSMTP.instances.clear()

    provider = EmailProvider(
        settings_for(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="user",
            smtp_password="pass",
            smtp_from="hermes@anton.local",
            smtp_to="ops@anton.local,admin@anton.local",
            smtp_use_tls=True,
        )
    )
    await provider.send(message({"subject": "[ERROR] Disk usage high", "body": "boom"}))

    server = FakeSMTP.instances[-1]
    assert server.host == "smtp.example.com"
    assert server.started_tls is True
    assert server.login_credentials == ("user", "pass")
    assert server.sent
    email = server.sent[-1]
    assert email["Subject"] == "[ERROR] Disk usage high"
    assert email["To"] == "ops@anton.local, admin@anton.local"


def test_email_disabled_without_config() -> None:
    assert not EmailProvider(settings_for()).enabled
    assert not EmailProvider(settings_for(smtp_host="h", smtp_from="f")).enabled
