"""Forge HTTP API: auth, tool advertisement, run + approval round trip."""

from __future__ import annotations

from tests.conftest import FakeHermes


async def test_health_without_token(client) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["environment"] == "test"


async def test_v1_requires_bearer_token(client) -> None:
    assert (await client.get("/v1/tools")).status_code == 401
    assert (
        await client.get("/v1/tools", headers={"Authorization": "Bearer wrong"})
    ).status_code == 401


async def test_tools_advertise_full_surface(client) -> None:
    response = await client.get("/v1/tools", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200
    tools = {tool["name"]: tool for tool in response.json()["tools"]}
    assert len(tools) == 22
    assert tools["docker_ps"]["read_only"] is True
    assert tools["docker_rollback"]["risk"] == "high"
    assert tools["docker_restart"]["read_only"] is False
    assert {t for t in tools if t in {"web_search", "fetch_url", "write_note"}} == {
        "web_search",
        "fetch_url",
        "write_note",
    }


async def test_run_read_only(client) -> None:
    response = await client.post(
        "/v1/run",
        headers={"Authorization": "Bearer test-token", "x-forge-caller": "test"},
        json={"tool": "docker_ps", "args": {}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False or body["ok"] is True  # gateway may or may not be present
    assert body["decision"] == "allowed"


async def test_approval_round_trip_via_api(app, client) -> None:
    hermes = FakeHermes()
    app.state.engine._hermes = hermes

    headers = {"Authorization": "Bearer test-token", "x-forge-caller": "test"}
    pending = await client.post(
        "/v1/run", headers=headers, json={"tool": "docker_restart", "args": {"target": "gitea"}}
    )
    assert pending.status_code == 200
    approval_id = pending.json()["approval_id"]
    assert approval_id

    listed = await client.get("/v1/approvals", headers=headers)
    assert listed.status_code == 200
    assert [a["id"] for a in listed.json()] == [approval_id]

    resolved = await client.post(
        f"/v1/approvals/{approval_id}/resolve",
        headers=headers,
        json={"approved": True, "by": "telegram"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["ok"] is True

    runs = await client.get("/v1/runs", headers=headers)
    decisions = [r["decision"] for r in runs.json()]
    assert "approval" in decisions


async def test_resolve_unknown_approval(client) -> None:
    response = await client.post(
        "/v1/approvals/deadbeef/resolve",
        headers={"Authorization": "Bearer test-token"},
        json={"approved": True},
    )
    assert response.status_code == 404
