"""Full-runtime integration: observers -> storage -> engines -> publishing."""

from __future__ import annotations

from app.core.runtime import SentinelRuntime
from tests.conftest import observation


class Recorder:
    def __init__(self) -> None:
        self.events = []

    async def publish(self, event) -> None:
        self.events.append(event)

    async def aclose(self) -> None:
        pass


async def test_runtime_end_to_end(settings_factory):
    settings = settings_factory(hermes_enabled=False)
    runtime = SentinelRuntime(settings)
    await runtime.start()
    try:
        await runtime.ingest_one(
            observation(
                source="network",
                object="device:98:9d:b2:a9:9f:8f",
                state="present",
                metadata={"mac": "98:9d:b2:a9:9f:8f", "ip": "192.168.1.1"},
            )
        )
        await runtime.ingest_one(
            observation(
                source="network",
                object="device:11:22:33:44:55:66",
                state="present",
                metadata={"mac": "11:22:33:44:55:66", "ip": "192.168.1.55"},
            )
        )
        await runtime.refresh_presence()

        observations, total = await runtime.repository.list_observations(limit=100)
        assert total >= 2

        devices, _ = await runtime.repository.list_devices(limit=100)
        by_key = {device.device_key: device for device in devices}
        assert by_key["98:9d:b2:a9:9f:8f"].known is True
        assert by_key["98:9d:b2:a9:9f:8f"].vendor == "AVM"
        assert by_key["11:22:33:44:55:66"].known is False

        presence = await runtime.repository.latest_presence()
        assert presence is not None
        assert presence.status.value in {"home_occupied", "nobody_home"}

        events, _ = await runtime.repository.list_device_events(limit=100)
        assert any(event["event"] == "joined" for event in events)
    finally:
        await runtime.stop()


async def test_publishing_paths(settings_factory):
    from tests.test_api import patched_config_dir

    settings = settings_factory(config_dir=patched_config_dir(stable_for=0), hermes_enabled=False)
    runtime = SentinelRuntime(settings)
    await runtime.start()
    recorder = Recorder()
    runtime.publisher = recorder
    try:
        await runtime.ingest_one(observation(source="router", object="gateway", state="offline"))
        event_types = {event.type for event in recorder.events}
        assert "situation_activated" in event_types

        await runtime.ingest_one(
            observation(
                source="network",
                object="device:a4:83:e7:12:34:56",
                state="present",
                metadata={"mac": "a4:83:e7:12:34:56", "ip": "192.168.1.40"},
            )
        )
        event_types = {event.type for event in recorder.events}
        assert "device_joined" in event_types

        await runtime.ingest_one(
            observation(
                source="network",
                object="device:11:22:33:44:55:66",
                state="present",
                metadata={"mac": "11:22:33:44:55:66", "ip": "192.168.1.55"},
            )
        )
        event_types = {event.type for event in recorder.events}
        assert "device_unknown_joined" in event_types

        await runtime.refresh_presence()
        event_types = {event.type for event in recorder.events}
        assert "presence_change" in event_types

        for event in recorder.events:
            assert event.module == "sentinel"
    finally:
        await runtime.stop()


async def test_reconcile_and_resolution(settings_factory):
    from tests.test_api import patched_config_dir

    settings = settings_factory(
        config_dir=patched_config_dir(stable_for=0, cooldown=0), hermes_enabled=False
    )
    runtime = SentinelRuntime(settings)
    await runtime.start()
    recorder = Recorder()
    runtime.publisher = recorder
    try:
        await runtime.ingest_one(
            observation(
                source="network",
                object="device:11:22:33:44:55:66",
                state="present",
                metadata={"mac": "11:22:33:44:55:66"},
            )
        )
        await runtime.ingest_one(
            observation(
                source="network",
                object="device:11:22:33:44:55:66",
                state="offline",
                metadata={"mac": "11:22:33:44:55:66"},
            )
        )
        event_types = {event.type for event in recorder.events}
        assert "device_left" in event_types

        situations, _ = await runtime.repository.list_situations()
        assert any(item.rule_id == "unknown_device_joined" for item in situations)
    finally:
        await runtime.stop()
