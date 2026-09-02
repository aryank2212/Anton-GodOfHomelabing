"""Device tracker tests: inventory, identity, lifecycle events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.config.loader import DeviceDefinition
from app.models.device import DeviceKind
from app.network.tracker import DeviceTracker
from app.network.vendors import VendorLookup
from app.presence.definitions import DeviceCatalog
from tests.conftest import observation


def catalog():
    return DeviceCatalog(
        [
            DeviceDefinition(name="Router", mac="98:9d:b2:a9:9f:8f", owner=None, category="router"),
            DeviceDefinition(name="Aryan's Phone", mac="aa:bb:cc:dd:ee:01", owner="Aryan"),
        ]
    )


def tracker() -> DeviceTracker:
    return DeviceTracker(catalog=catalog(), vendor_lookup=VendorLookup())


async def test_new_unknown_device_joins():
    t = tracker()
    obs = observation(
        source="network",
        category="network",
        object="device:11:22:33:44:55:66",
        state="present",
        metadata={"mac": "11:22:33:44:55:66", "ip": "192.168.1.20"},
    )
    changed, events = await t.feed(obs)
    assert len(changed) == 1
    device = changed[0]
    assert device.known is False
    assert device.device_key == "11:22:33:44:55:66"
    assert device.online is True
    assert [e.event for e in events] == ["joined"]


async def test_known_device_resolves_vendor_and_name():
    t = tracker()
    obs = observation(
        source="network",
        object="device:98:9d:b2:a9:9f:8f",
        state="present",
        metadata={"mac": "98:9d:b2:a9:9f:8f", "ip": "192.168.1.1"},
    )
    changed, _ = await t.feed(obs)
    device = changed[0]
    assert device.known is True
    assert device.vendor == "AVM"
    assert device.name == "Router"
    assert device.category == DeviceKind.ROUTER


async def test_mac_normalization_and_ip_accumulation():
    t = tracker()
    await t.feed(
        observation(
            object="device:11:22:33:44:55:66",
            state="present",
            metadata={"mac": "11:22:33:44:55:66", "ip": "192.168.1.20"},
        )
    )
    changed, _ = await t.feed(
        observation(
            object="device:11:22:33:44:55:66",
            state="online",
            metadata={"mac": "11-22-33-44-55-66", "ip": "192.168.1.21"},
        )
    )
    assert len(changed) == 1
    assert changed[0].ips == ["192.168.1.20", "192.168.1.21"]


async def test_offline_transition_emits_left():
    t = tracker()
    await t.feed(
        observation(
            object="device:11:22:33:44:55:66",
            state="present",
            metadata={"mac": "11:22:33:44:55:66"},
        )
    )
    _, events = await t.feed(
        observation(
            object="device:11:22:33:44:55:66",
            state="offline",
            metadata={"mac": "11:22:33:44:55:66"},
        )
    )
    assert [e.event for e in events] == ["left"]


async def test_non_device_observation_ignored():
    t = tracker()
    changed, events = await t.feed(observation(source="system", object="cpu", state="high"))
    assert changed == []
    assert events == []


def test_identity_key_precedence():
    assert tracker()._identity_key({"mac": "aa:bb", "ip": "1.1.1.1"}) == "aa:bb"
    assert tracker()._identity_key({"ip": "1.1.1.1"}) == "ip:1.1.1.1"
    assert tracker()._identity_key({"hostname": "host"}) == "host:host"


def test_reconcile_online_marks_stale_offline():
    t = tracker()
    now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
    t._by_key["aa:bb"] = DeviceFactory(now=now)
    events = t.reconcile_online(now=now + timedelta(seconds=200))
    assert len(events) == 1
    assert events[0].event == "left"
    assert t.get("aa:bb").online is False


def DeviceFactory(now):
    from app.models.device import Device

    return Device(
        device_key="aa:bb",
        mac="aa:bb",
        online=True,
        last_seen=now,
        updated_at=now,
        confidence=0.9,
    )
