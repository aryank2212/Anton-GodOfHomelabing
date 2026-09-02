from __future__ import annotations

from tests.helpers import make_payload, outcome_is, state_is, wait_until


async def test_create_event_returns_202_with_id(client) -> None:
    response = await client.post("/event", json=make_payload())
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["id"]


async def test_event_is_stored_and_processed(client, state) -> None:
    event_id = (await client.post("/event", json=make_payload())).json()["id"]
    await wait_until(lambda: state_is(state, event_id, "done"))

    assert await outcome_is(state, event_id, "logged")


async def test_accepts_minimal_payload(client) -> None:
    response = await client.post(
        "/event", json={"module": "legacy", "type": "restart", "title": "Restarted"}
    )
    assert response.status_code == 202


async def test_rejects_empty_module(client) -> None:
    response = await client.post("/event", json={"module": "", "type": "x", "title": "t"})
    assert response.status_code == 422


async def test_rejects_unknown_fields(client) -> None:
    payload = make_payload(hax="nope")
    response = await client.post("/event", json=payload)
    assert response.status_code == 422


async def test_accepts_correlation_id(client) -> None:
    correlation_id = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
    response = await client.post("/event", json=make_payload(correlation_id=correlation_id))
    assert response.status_code == 202


async def test_list_events_pagination(client) -> None:
    for index in range(5):
        await client.post("/event", json=make_payload(title=f"event {index}"))

    response = await client.get("/events", params={"limit": 2, "offset": 0})
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["pagination"]["total"] == 5
    assert body["pagination"]["next_offset"] == 2


async def test_list_events_ends_pagination(client) -> None:
    for index in range(3):
        await client.post("/event", json=make_payload(title=f"event {index}"))

    response = await client.get("/events", params={"limit": 10, "offset": 0})
    body = response.json()
    assert body["pagination"]["total"] == 3
    assert body["pagination"]["next_offset"] is None


async def test_list_events_filters(client) -> None:
    await client.post("/event", json=make_payload(module="watcher"))
    await client.post(
        "/event", json=make_payload(module="phoenix", type="restart", severity="error")
    )

    by_module = (await client.get("/events", params={"module": "phoenix"})).json()
    assert by_module["pagination"]["total"] == 1
    assert by_module["items"][0]["module"] == "phoenix"

    by_severity = (await client.get("/events", params={"severity": "error"})).json()
    assert by_severity["pagination"]["total"] == 1
    assert by_severity["items"][0]["severity"] == "error"

    by_type = (await client.get("/events", params={"type": "restart"})).json()
    assert by_type["pagination"]["total"] == 1
    assert by_type["items"][0]["type"] == "restart"


async def test_list_events_limits_to_configured_max(client) -> None:
    for index in range(5):
        await client.post("/event", json=make_payload(title=f"event {index}"))

    response = await client.get("/events", params={"limit": 1000})
    body = response.json()
    assert body["pagination"]["limit"] <= 100
