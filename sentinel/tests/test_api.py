"""API endpoint tests through the full runtime lifespan."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import yaml

from tests.conftest import observation

CONFIG_SRC = Path(__file__).resolve().parent.parent / "app" / "config"


def patched_config_dir(**rule_changes) -> str:
    """Copy app/config and patch every rule with the given keys."""
    dst = Path(tempfile.mkdtemp()) / "config"
    shutil.copytree(CONFIG_SRC, dst)
    rules_path = dst / "rules.yaml"
    doc = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    for rule in doc["rules"]:
        for key, value in rule_changes.items():
            if key in rule:
                rule[key] = value
    rules_path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(dst)


async def test_health(api):
    response = await api.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["version"] == "1.0.0"
    assert body["observations"] == 0


async def test_observations_empty(api):
    response = await api.get("/observations")
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["pagination"]["total"] == 0


async def test_observations_roundtrip_and_filters(api, app_and_runtime):
    runtime = app_and_runtime.state.runtime
    await runtime.ingest_one(
        observation(source="router", object="gateway", state="offline", tags=["wan"])
    )
    await runtime.ingest_one(
        observation(source="system", object="cpu", state="high", confidence=0.8)
    )

    body = (await api.get("/observations")).json()
    assert body["pagination"]["total"] == 2

    filtered = (await api.get("/observations", params={"state": "offline"})).json()
    assert filtered["pagination"]["total"] == 1
    assert filtered["items"][0]["object"] == "gateway"

    tagged = (await api.get("/observations", params={"tag": "wan"})).json()
    assert tagged["pagination"]["total"] == 1

    observation_id = body["items"][0]["observation_id"]
    assert (await api.get(f"/observations/{observation_id}")).status_code == 200
    assert (await api.get("/observations/not-a-uuid")).status_code == 400
    assert (await api.get("/observations/00000000-0000-0000-0000-000000000000")).status_code == 404


async def test_observations_pagination(api, app_and_runtime):
    runtime = app_and_runtime.state.runtime
    for index in range(3):
        await runtime.ingest_one(observation(source="test", object=f"item-{index}", state="online"))
    first = (await api.get("/observations", params={"limit": 2})).json()
    assert first["pagination"]["total"] == 3
    assert first["pagination"]["next_offset"] == 2
    assert len(first["items"]) == 2
    second = (await api.get("/observations", params={"limit": 2, "offset": 2})).json()
    assert second["pagination"]["next_offset"] is None
    assert len(second["items"]) == 1


async def test_situations_activate_and_link_observations(settings_factory):
    import httpx

    from app.main import create_app

    settings = settings_factory(config_dir=patched_config_dir(stable_for=0))
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
            runtime = app.state.runtime
            await runtime.ingest_one(
                observation(source="router", object="gateway", state="offline")
            )
            await runtime.ingest_one(observation(source="ups", object="ups", state="on_battery"))
            await runtime._correlation_tick()

            body = (await api.get("/situations")).json()
            rule_ids = {item["rule_id"] for item in body["items"]}
            assert "power_outage" in rule_ids
            power = next(item for item in body["items"] if item["rule_id"] == "power_outage")
            assert power["severity"] == "critical"

            situation_id = power["situation_id"]
            derived = (await api.get(f"/situations/{situation_id}/observations")).json()
            assert {item["source"] for item in derived} == {"router", "ups"}

            assert (await api.get("/situations/not-a-uuid")).status_code == 400


async def test_presence_endpoint(api, app_and_runtime):
    runtime = app_and_runtime.state.runtime
    await runtime.ingest_one(
        observation(
            source="network",
            object="device:a4:83:e7:12:34:56",
            state="present",
            metadata={"mac": "a4:83:e7:12:34:56", "ip": "192.168.1.40"},
        )
    )
    await runtime.refresh_presence()
    body = (await api.get("/presence")).json()
    assert body["state"]["status"] == "home_occupied"
    assert body["state"]["people"] == ["Aryan"]
    assert body["devices"]["online_count"] == 1

    history = (await api.get("/presence/history")).json()
    assert history["pagination"]["total"] >= 1


async def test_devices_endpoint(api, app_and_runtime):
    runtime = app_and_runtime.state.runtime
    await runtime.ingest_one(
        observation(
            source="network",
            object="device:11:22:33:44:55:66",
            state="present",
            metadata={"mac": "11:22:33:44:55:66", "ip": "192.168.1.55"},
        )
    )
    body = (await api.get("/devices")).json()
    assert body["pagination"]["total"] == 1
    device = body["items"][0]
    key = device["device_key"]
    assert device["known"] is False

    by_key = (await api.get(f"/devices/{key}")).json()
    assert by_key["device_key"] == key

    history = (await api.get(f"/devices/{key}/history")).json()
    assert history["pagination"]["total"] == 1
    assert history["items"][0]["event"] == "joined"

    assert (await api.get("/devices/not-there")).status_code == 404
    assert (await api.get("/devices/not-there/history")).status_code == 404


async def test_observers_endpoint_with_none_configured(api):
    body = (await api.get("/observers")).json()
    assert body == {"observers": [], "running": False}


async def test_unknown_route_returns_404(api):
    assert (await api.get("/does-not-exist")).status_code == 404
