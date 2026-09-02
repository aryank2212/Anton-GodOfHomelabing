from __future__ import annotations

import json

import httpx
from app.config import Settings
from app.forge import ForgeClient
from app.main import create_app
from app.ollama import OllamaClient
from fastapi.testclient import TestClient


def settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "_env_file": None,
        "shared_token": "sekret",
        "model": "qwen3:1.7b",
        "forge_url": "http://forge.test:8000",
        "forge_token": "forgesekret",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def make_ollama(responses: list[str]) -> tuple[OllamaClient, list[dict]]:
    """Ollama client that replays the given contents in order; ``captured``
    records every chat payload so tests can assert on the messages sent."""
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured.append(payload)
        index = min(len(captured) - 1, len(responses) - 1)
        return httpx.Response(
            200,
            json={
                "model": "qwen3:1.7b",
                "message": {"role": "assistant", "content": responses[index]},
                "prompt_eval_count": 10,
                "eval_count": 3,
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OllamaClient(settings(), client=client), captured


def make_forge(handler) -> ForgeClient:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ForgeClient(settings(), client=client)


CATALOG = {
    "tools": [
        {
            "name": "docker_ps",
            "description": "list running containers",
            "parameters": {"target": {"type": "string", "description": "filter"}},
            "risk": "low",
            "read_only": True,
        },
        {
            "name": "docker_restart",
            "description": "restart a container",
            "parameters": {"target": {"type": "string"}},
            "risk": "medium",
            "read_only": False,
        },
    ]
}


def forge_ok(request: httpx.Request) -> httpx.Response:
    if request.method == "GET":
        return httpx.Response(200, json=CATALOG)
    body = json.loads(request.content) if request.content else {}
    return httpx.Response(
        200,
        json={
            "ok": True,
            "tool": body["tool"],
            "output": "container uptime-kuma restarted",
            "decision": "allowed",
        },
    )


def test_agent_answers_without_tools() -> None:
    ollama, _ = make_ollama(["Everything is fine."])
    client = TestClient(create_app(settings(), ollama=ollama, forge=make_forge(forge_ok)))
    with client:
        resp = client.post(
            "/v1/agent",
            json={"message": "how is the lab?", "history": []},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "Everything is fine."
    assert body["steps"] == 0
    assert body["tools"] == []


def test_agent_calls_tool_then_answers() -> None:
    calls: list[dict] = []
    tool_call = json.dumps({"tool": "docker_ps", "args": {"target": "uptime-kuma"}})

    def forge_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            calls.append(json.loads(request.content) if request.content else {})
        return forge_ok(request)

    ollama, _captured = make_ollama([tool_call, "uptime-kuma is running."])
    client = TestClient(create_app(settings(), ollama=ollama, forge=make_forge(forge_handler)))
    with client:
        resp = client.post(
            "/v1/agent",
            json={"message": "is uptime-kuma up?", "history": []},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "uptime-kuma is running."
    assert body["steps"] == 1
    assert body["tools"][0]["tool"] == "docker_ps"
    assert body["tools"][0]["decision"] == "allowed"
    assert body["tools"][0]["ok"] is True
    assert calls[0]["tool"] == "docker_ps"
    assert calls[0]["args"] == {"target": "uptime-kuma"}
    assert calls[0]["reason"].startswith("oracle agent:")


def test_agent_feeds_tool_result_back_to_model() -> None:
    tool_call = json.dumps({"tool": "docker_ps", "args": {}})

    def forge_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=CATALOG)
        return httpx.Response(
            200,
            json={"ok": True, "tool": "docker_ps", "output": "1 running", "decision": "allowed"},
        )

    ollama, captured = make_ollama([tool_call, "There is 1 container."])
    client = TestClient(create_app(settings(), ollama=ollama, forge=make_forge(forge_handler)))
    with client:
        resp = client.post(
            "/v1/agent",
            json={"message": "list containers", "history": []},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 200
    second_messages = captured[1]["messages"]
    assert second_messages[-1]["role"] == "user"
    assert "Tool docker_ps result" in second_messages[-1]["content"]
    assert "1 running" in second_messages[-1]["content"]


def test_agent_tolerates_code_fenced_tool_call() -> None:
    fenced = '```\n{"tool": "docker_ps", "args": {}}\n```'

    ollama, _ = make_ollama([fenced, "done"])
    client = TestClient(create_app(settings(), ollama=ollama, forge=make_forge(forge_ok)))
    with client:
        resp = client.post(
            "/v1/agent",
            json={"message": "check", "history": []},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 200
    assert resp.json()["steps"] == 1


def test_agent_blocks_unknown_tool_without_calling_forge() -> None:
    forge_calls: list[httpx.Request] = []
    tool_call = json.dumps({"tool": "rm_rf", "args": {"path": "/"}})

    def forge_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            forge_calls.append(request)
        return forge_ok(request)

    ollama, captured = make_ollama([tool_call, "That tool is not available."])
    client = TestClient(create_app(settings(), ollama=ollama, forge=make_forge(forge_handler)))
    with client:
        resp = client.post(
            "/v1/agent",
            json={"message": "delete everything", "history": []},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tools"][0]["decision"] == "blocked"
    assert "unknown tool" in body["tools"][0]["error"]
    assert forge_calls == []
    assert "unknown tool 'rm_rf'" in captured[1]["messages"][-1]["content"]


def test_agent_sends_forge_bearer_token() -> None:
    headers: list[dict] = []
    tool_call = json.dumps({"tool": "docker_ps", "args": {}})

    def forge_handler(request: httpx.Request) -> httpx.Response:
        headers.append(dict(request.headers))
        return forge_ok(request)

    ollama, _ = make_ollama([tool_call, "ok"])
    client = TestClient(create_app(settings(), ollama=ollama, forge=make_forge(forge_handler)))
    with client:
        resp = client.post(
            "/v1/agent",
            json={"message": "check", "history": []},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 200
    assert headers[0]["authorization"] == "Bearer forgesekret"


def test_agent_max_steps_terminates() -> None:
    tool_call = json.dumps({"tool": "docker_ps", "args": {}})
    many = [tool_call] * 10

    def forge_handler(request: httpx.Request) -> httpx.Response:
        return forge_ok(request)

    ollama, _ = make_ollama(many)
    client = TestClient(
        create_app(settings(agent_max_steps=3), ollama=ollama, forge=make_forge(forge_handler))
    )
    with client:
        resp = client.post(
            "/v1/agent",
            json={"message": "loop forever", "history": []},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["steps"] == 3
    assert len(body["tools"]) == 3


def test_agent_503_without_forge_token() -> None:
    ollama, _ = make_ollama(["hi"])
    client = TestClient(
        create_app(settings(forge_token=None), ollama=ollama, forge=make_forge(forge_ok))
    )
    with client:
        resp = client.post(
            "/v1/agent",
            json={"message": "check", "history": []},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 503


def test_agent_502_when_forge_unreachable() -> None:
    def forge_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    ollama, _ = make_ollama(["hi"])
    client = TestClient(create_app(settings(), ollama=ollama, forge=make_forge(forge_handler)))
    with client:
        resp = client.post(
            "/v1/agent",
            json={"message": "check", "history": []},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 502


def test_agent_502_when_ollama_fails() -> None:
    def ollama_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client_http = httpx.AsyncClient(transport=httpx.MockTransport(ollama_handler))
    ollama = OllamaClient(settings(), client=client_http)
    client = TestClient(create_app(settings(), ollama=ollama, forge=make_forge(forge_ok)))
    with client:
        resp = client.post(
            "/v1/agent",
            json={"message": "check", "history": []},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 502


def test_agent_requires_token() -> None:
    ollama, _ = make_ollama(["hi"])
    client = TestClient(create_app(settings(), ollama=ollama, forge=make_forge(forge_ok)))
    with client:
        missing = client.post("/v1/agent", json={"message": "check", "history": []})
        wrong = client.post(
            "/v1/agent",
            json={"message": "check", "history": []},
            headers={"Authorization": "Bearer nope"},
        )
    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_agent_uses_agent_temperature() -> None:
    tool_call = json.dumps({"tool": "docker_ps", "args": {}})

    ollama, captured = make_ollama([tool_call, "ok"])
    client = TestClient(create_app(settings(), ollama=ollama, forge=make_forge(forge_ok)))
    with client:
        resp = client.post(
            "/v1/agent",
            json={"message": "check", "history": []},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 200
    assert captured[0]["options"]["temperature"] == 0.2


def test_agent_injects_catalog_and_context() -> None:
    ollama, captured = make_ollama(["I see it."])
    client = TestClient(create_app(settings(), ollama=ollama, forge=make_forge(forge_ok)))
    with client:
        resp = client.post(
            "/v1/agent",
            json={
                "message": "what is running?",
                "history": [],
                "context": "live snapshot: all ok",
            },
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 200
    system = captured[0]["messages"][0]["content"]
    assert "docker_restart" in system
    assert "docker_ps" in system
    roles = [m["role"] for m in captured[0]["messages"]]
    assert roles == ["system", "system", "user"]
    assert captured[0]["messages"][1]["content"] == "live snapshot: all ok"


def test_agent_forge_rejection_becomes_tool_result() -> None:
    def forge_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=CATALOG)
        return httpx.Response(
            200,
            json={
                "ok": False,
                "tool": "docker_restart",
                "output": "requires approval",
                "decision": "approval",
                "approval_id": "abc123",
            },
        )

    tool_call = json.dumps({"tool": "docker_restart", "args": {"target": "uptime-kuma"}})
    ollama, captured = make_ollama([tool_call, "Waiting on approval."])
    client = TestClient(create_app(settings(), ollama=ollama, forge=make_forge(forge_handler)))
    with client:
        resp = client.post(
            "/v1/agent",
            json={"message": "restart uptime-kuma", "history": []},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tools"][0]["decision"] == "approval"
    assert body["tools"][0]["approval_id"] == "abc123"
    assert "approval_id=abc123" in captured[1]["messages"][-1]["content"]


def test_agent_truncates_long_tool_output() -> None:
    long_output = "x" * 9000

    def forge_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=CATALOG)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "tool": "docker_ps",
                "output": long_output,
                "decision": "allowed",
            },
        )

    tool_call = json.dumps({"tool": "docker_ps", "args": {}})
    ollama, captured = make_ollama([tool_call, "done."])
    client = TestClient(
        create_app(
            settings(agent_tool_output_limit=500),
            ollama=ollama,
            forge=make_forge(forge_handler),
        )
    )
    with client:
        resp = client.post(
            "/v1/agent",
            json={"message": "check", "history": []},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 200
    fed_back = captured[1]["messages"][-1]["content"]
    assert "[truncated 8500 more chars]" in fed_back
    assert len(fed_back) < 600


def test_agent_truncation_disabled_when_zero() -> None:
    long_output = "y" * 5000

    def forge_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=CATALOG)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "tool": "docker_ps",
                "output": long_output,
                "decision": "allowed",
            },
        )

    tool_call = json.dumps({"tool": "docker_ps", "args": {}})
    ollama, captured = make_ollama([tool_call, "done."])
    client = TestClient(
        create_app(
            settings(agent_tool_output_limit=0),
            ollama=ollama,
            forge=make_forge(forge_handler),
        )
    )
    with client:
        resp = client.post(
            "/v1/agent",
            json={"message": "check", "history": []},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 200
    fed_back = captured[1]["messages"][-1]["content"]
    assert "truncated" not in fed_back
    assert long_output in fed_back


def test_agent_context_budget_stops_tools() -> None:
    tool_call = json.dumps({"tool": "docker_ps", "args": {}})

    def forge_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=CATALOG)
        return httpx.Response(
            200,
            json={"ok": True, "tool": "docker_ps", "output": "z" * 2000, "decision": "allowed"},
        )

    ollama, captured = make_ollama([tool_call, tool_call, "Final answer."])
    client = TestClient(
        create_app(
            settings(agent_context_budget=1500, agent_max_steps=10),
            ollama=ollama,
            forge=make_forge(forge_handler),
        )
    )
    with client:
        resp = client.post(
            "/v1/agent",
            json={"message": "check", "history": []},
            headers={"Authorization": "Bearer sekret"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "Final answer."
    assert body["steps"] == 1
    assert "context" in captured[-1]["messages"][-1]["content"]
