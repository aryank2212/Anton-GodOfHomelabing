from __future__ import annotations


async def test_incidents_empty(client) -> None:
    response = await client.get("/incidents")
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["limit"] == 50
    assert body["offset"] == 0


async def test_incidents_created_and_listed(client, state) -> None:
    incident = await state.incidents.open(
        component="web",
        failure_type="unreachable",
        severity="error",
        metadata={"monitor": "web_http"},
    )

    response = await client.get("/incidents")
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["incident_id"] == incident.incident_id
    assert body["items"][0]["component"] == "web"
    assert body["items"][0]["failure_type"] == "unreachable"
    assert body["items"][0]["metadata"]["monitor"] == "web_http"


async def test_incidents_filter_and_pagination(client, state) -> None:
    for _ in range(3):
        await state.incidents.open(component="web", failure_type="unreachable")
    await state.incidents.open(component="web", failure_type="timeout")

    page = await client.get("/incidents", params={"limit": 2, "offset": 0})
    body = page.json()
    assert body["total"] == 4
    assert len(body["items"]) == 2

    filtered = await client.get("/incidents", params={"failureType": "timeout"})
    assert filtered.json()["total"] == 1

    component = await client.get("/incidents", params={"component": "nope"})
    assert component.json()["total"] == 0


async def test_get_single_incident(client, state) -> None:
    incident = await state.incidents.open(component="web", failure_type="unreachable")
    response = await client.get(f"/incidents/{incident.incident_id}")
    assert response.status_code == 200
    assert response.json()["incident_id"] == incident.incident_id


async def test_get_missing_incident_404(client) -> None:
    response = await client.get("/incidents/nope")
    assert response.status_code == 404


async def test_recover_endpoint(client) -> None:
    """POST /recover runs the workflow; the endpoint is unreachable here so the
    incident ends unresolved."""
    response = await client.post("/recover/web")
    assert response.status_code == 200
    body = response.json()
    assert body["component"] == "web"
    assert body["detected_by"] == "manual"
    assert body["status"] == "unresolved"
    assert body["recovery_strategy"] == "http_retry"


async def test_recover_unknown_component_404(client) -> None:
    response = await client.post("/recover/does-not-exist")
    assert response.status_code == 404


async def test_maintenance_workflow(client) -> None:
    created = await client.post("/maintenance", json={"component": "web", "reason": "upgrade"})
    assert created.status_code == 201
    entry = created.json()
    assert entry["component"] == "web"
    assert entry["active"] is True

    listed = await client.get("/maintenance")
    assert listed.status_code == 200
    assert any(item["id"] == entry["id"] for item in listed.json())

    closed = await client.delete(f"/maintenance/{entry['id']}")
    assert closed.status_code == 204
