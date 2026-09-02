from __future__ import annotations


async def test_health_ok(client) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["version"]
    assert body["environment"] == "test"
    assert body["scheduler"]["enabled"] is False
    assert body["scheduler"]["monitors"] >= 1
    assert "web_http" in body["monitors"]
    assert body["hermes"]["enabled"] is False


async def test_health_returns_request_id(client) -> None:
    response = await client.get("/health")
    assert "x-request-id" in response.headers
    assert response.headers["x-request-id"]
