from __future__ import annotations

import json

import httpx
from app.config import Settings
from app.main import create_app
from app.ollama import OllamaClient
from app.schemas import Turn
from fastapi.testclient import TestClient


def settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "_env_file": None,
        "shared_token": "sekret",
        "model": "qwen3:1.7b",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def make_ollama(handler) -> OllamaClient:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OllamaClient(settings(), client=client)


def ok_chat(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "qwen3:1.7b",
            "message": {"role": "assistant", "content": "Hello from Ollama!"},
            "prompt_eval_count": 12,
            "eval_count": 4,
        },
    )


def test_health_reports_up() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": []})

    client = TestClient(create_app(settings(), ollama=make_ollama(handler)))
    with client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "model": "qwen3:1.7b", "ollama": "up"}


def test_health_reports_ollama_down() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = TestClient(create_app(settings(), ollama=make_ollama(handler)))
    with client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["ollama"] == "down"


def test_ask_returns_answer() -> None:
    client = TestClient(create_app(settings(), ollama=make_ollama(ok_chat)))
    with client:
        resp = client.post(
            "/v1/ask",
            json={"message": "hi", "history": []},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "Hello from Ollama!"
    assert body["model"] == "qwen3:1.7b"
    assert body["tokens"] == {"prompt": 12, "completion": 4}


def test_ask_requires_token() -> None:
    client = TestClient(create_app(settings(), ollama=make_ollama(ok_chat)))
    with client:
        missing = client.post("/v1/ask", json={"message": "hi", "history": []})
        wrong = client.post(
            "/v1/ask",
            json={"message": "hi", "history": []},
            headers={"Authorization": "Bearer nope"},
        )
    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_ask_without_token_configured_does_not_require_auth() -> None:
    client = TestClient(create_app(settings(shared_token=None), ollama=make_ollama(ok_chat)))
    with client:
        resp = client.post("/v1/ask", json={"message": "hi", "history": []})
    assert resp.status_code == 200


def test_ask_builds_system_and_history_messages() -> None:
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return ok_chat(request)

    history = [Turn(role="user", content=f"q{i}") for i in range(20)]
    client = TestClient(create_app(settings(max_history=3), ollama=make_ollama(handler)))
    with client:
        resp = client.post(
            "/v1/ask",
            json={
                "message": "final",
                "history": [turn.model_dump() for turn in history],
            },
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 200
    payload = captured[0]
    roles = [m["role"] for m in payload["messages"]]
    assert roles == ["system", "user", "user", "user", "user"]
    assert payload["messages"][-1]["content"] == "final"
    assert payload["messages"][1]["content"] == "q17"  # only the last 3 of 20 kept


def test_ask_injects_context_as_system_message() -> None:
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return ok_chat(request)

    client = TestClient(create_app(settings(), ollama=make_ollama(handler)))
    with client:
        resp = client.post(
            "/v1/ask",
            json={
                "message": "hi",
                "history": [],
                "context": "Hermes live state at now UTC: all ok",
            },
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 200
    payload = captured[0]
    roles = [m["role"] for m in payload["messages"]]
    assert roles == ["system", "system", "user"]
    assert payload["messages"][1]["content"] == "Hermes live state at now UTC: all ok"


def test_ask_502_when_ollama_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = TestClient(create_app(settings(), ollama=make_ollama(handler)))
    with client:
        resp = client.post(
            "/v1/ask",
            json={"message": "hi", "history": []},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 502


def test_ask_rejects_invalid_history_role() -> None:
    client = TestClient(create_app(settings(), ollama=make_ollama(ok_chat)))
    with client:
        resp = client.post(
            "/v1/ask",
            json={"message": "hi", "history": [{"role": "robot", "content": "x"}]},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 422


def test_decide_returns_model_reply() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "qwen3:1.7b",
                "message": {
                    "role": "assistant",
                    "content": (
                        '{"action": "docker_restart", "target": "jellyfin",'
                        ' "risk": "low", "reason": "crashed"}'
                    ),
                },
                "prompt_eval_count": 5,
                "eval_count": 9,
            },
        )

    client = TestClient(create_app(settings(), ollama=make_ollama(handler)))
    with client:
        resp = client.post(
            "/v1/decide",
            json={"situation": "jellyfin is down"},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "docker_restart" in body["reply"]
    assert body["tokens"] == {"prompt": 5, "completion": 9}


def test_decide_requires_token() -> None:
    client = TestClient(create_app(settings(), ollama=make_ollama(ok_chat)))
    with client:
        resp = client.post(
            "/v1/decide",
            json={"situation": "x"},
            headers={"Authorization": "Bearer nope"},
        )
    assert resp.status_code == 401


def test_decide_uses_decision_prompt_and_temperature() -> None:
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return ok_chat(request)

    client = TestClient(create_app(settings(), ollama=make_ollama(handler)))
    with client:
        resp = client.post(
            "/v1/decide",
            json={"situation": "watcher says disk full"},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 200
    payload = captured[0]
    assert payload["model"] == "qwen3:1.7b"
    assert payload["options"]["temperature"] == 0.2
    roles = [m["role"] for m in payload["messages"]]
    assert roles == ["system", "user"]
    assert "operations watchdog" in payload["messages"][0]["content"]
    assert payload["messages"][1]["content"] == "watcher says disk full"


def test_decide_502_when_ollama_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = TestClient(create_app(settings(), ollama=make_ollama(handler)))
    with client:
        resp = client.post(
            "/v1/decide",
            json={"situation": "x"},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 502
