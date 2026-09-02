"""Tests for the Oracle client contract: message caps, retries and parsing.

The client is the single owner of the gateway contract — the 8000-char limit
and the retry policy — so these tests pin that behaviour down hard.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.config.settings import Settings
from app.core.oracle import (
    ORACLE_MESSAGE_LIMIT,
    SAFE_MESSAGE_LIMIT,
    OracleClient,
    OracleError,
    OracleProtocolError,
    OracleUnavailableError,
    _parse_json,
)


def _settings() -> Settings:
    return Settings(oracle_enabled=True, oracle_retry_attempts=2, oracle_retry_backoff=0.0)


def _client(handler) -> OracleClient:
    transport = httpx.MockTransport(handler)
    return OracleClient(_settings(), client=httpx.AsyncClient(transport=transport))


async def _reply(client: OracleClient, message: str = "hello") -> str:
    return await client.ask(message)


# ------------------------------------------------------------------- success


def test_contract_constants_are_single_sourced() -> None:
    assert ORACLE_MESSAGE_LIMIT == 8000
    assert SAFE_MESSAGE_LIMIT == 8000 - 100


@pytest.mark.asyncio
async def test_ask_returns_reply_and_calls_once() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/v1/ask"
        return httpx.Response(200, json={"reply": "answer"})

    client = _client(handler)
    assert await _reply(client) == "answer"
    assert calls == 1


@pytest.mark.asyncio
async def test_ask_attaches_bearer_token() -> None:
    seen_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json={"reply": "ok"})

    settings = Settings(
        oracle_enabled=True, oracle_token="secret-token", oracle_retry_attempts=0
    )
    transport = httpx.MockTransport(handler)
    client = OracleClient(settings, client=httpx.AsyncClient(transport=transport))
    await client.ask("hi")
    assert seen_auth == ["Bearer secret-token"]


# ------------------------------------------------------------------- retries


@pytest.mark.asyncio
async def test_transient_5xx_is_retried_then_succeeds() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        # retry_attempts=2 -> up to 3 attempts total.
        if calls < 3:
            return httpx.Response(503, text="overloaded")
        return httpx.Response(200, json={"reply": "recovered"})

    client = _client(handler)
    assert await _reply(client) == "recovered"
    assert calls == 3


@pytest.mark.asyncio
async def test_network_failure_retries_then_raises_unavailable() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("tailnet link down")

    client = _client(handler)
    with pytest.raises(OracleUnavailableError):
        await _reply(client)
    assert calls == 3  # exhausted every retry


@pytest.mark.asyncio
async def test_client_error_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, text="bad prompt")

    client = _client(handler)
    with pytest.raises(OracleError):
        await _reply(client)
    assert calls == 1


# ----------------------------------------------------------------- protocols


@pytest.mark.asyncio
async def test_non_json_reply_is_a_hard_protocol_error() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text="this is not JSON")

    client = _client(handler)
    with pytest.raises(OracleProtocolError):
        await _reply(client)
    assert calls == 1  # a good HTTP response is never retried


@pytest.mark.asyncio
async def test_missing_reply_key_is_a_protocol_error() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"answer": "not the shape we expect"})

    client = _client(handler)
    with pytest.raises(OracleProtocolError):
        await _reply(client)
    assert calls == 1


# ------------------------------------------------------------- JSON helpers


def test_parse_json_tolerates_fences() -> None:
    assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json('{"a": 1}') == {"a": 1}
    assert _parse_json("not json at all") is None


@pytest.mark.asyncio
async def test_extract_entities_returns_only_entities() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert len(payload["message"]) <= 8000
        return httpx.Response(
            200,
            json={
                "reply": json.dumps(
                    {
                        "entities": [
                            {"name": "Alice", "kind": "person", "confidence": 0.9},
                            "garbage entry",
                        ]
                    }
                )
            },
        )

    client = _client(handler)
    entities = await client.extract_entities("text about Alice")
    assert len(entities) == 1
    assert entities[0]["name"] == "Alice"


@pytest.mark.asyncio
async def test_garbage_extraction_degrades_to_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"reply": "I have no idea"})

    client = _client(handler)
    assert await client.extract_entities("anything") == []
