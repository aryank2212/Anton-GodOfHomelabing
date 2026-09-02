"""Presence engine tests."""

from __future__ import annotations

from app.models.device import Device, PresenceStatus
from app.models.observation import utcnow
from app.presence.engine import PresenceEngine
from tests.conftest import observation as make_observation  # noqa: F401


def device(**overrides) -> Device:
    defaults = dict(
        device_key="aa:bb:cc:dd:ee:ff",
        known=True,
        owner="Aryan",
        online=True,
        confidence=0.9,
        last_seen=utcnow(),
    )
    defaults.update(overrides)
    return Device(**defaults)


def test_home_occupied_with_known_owner():
    engine = PresenceEngine()
    change, obs = engine.recompute([device()])
    assert change is not None
    assert change.current.status == PresenceStatus.HOME_OCCUPIED
    assert change.current.people == ["Aryan"]
    assert obs.object == "house"
    assert obs.state == PresenceStatus.HOME_OCCUPIED.value
    assert "presence" in obs.tags


def test_multiple_users():
    engine = PresenceEngine()
    fleet = [
        device(device_key="a", owner="Aryan"),
        device(device_key="b", owner="Rohan", mac="bb:bb:bb:bb:bb:bb"),
    ]
    change, _ = engine.recompute(fleet)
    assert change.current.status == PresenceStatus.MULTIPLE_USERS
    assert sorted(change.current.people) == ["Aryan", "Rohan"]


def test_unknown_present_when_only_unknown_online():
    engine = PresenceEngine()
    change, _ = engine.recompute([device(known=False, owner=None)])
    assert change.current.status == PresenceStatus.UNKNOWN_PRESENT
    assert change.current.unknown_devices == ["aa:bb:cc:dd:ee:ff"]


def test_nobody_home():
    engine = PresenceEngine(empty_confidence=0.7)
    change, obs = engine.recompute([])
    assert change.current.status == PresenceStatus.NOBODY_HOME
    assert change.current.confidence == 0.7
    assert obs.state == PresenceStatus.NOBODY_HOME.value


def test_change_only_on_transition():
    engine = PresenceEngine()
    engine.recompute([device()])
    change, _ = engine.recompute([device()])
    assert change is None


def test_offline_devices_do_not_count():
    engine = PresenceEngine()
    change, _ = engine.recompute([device(online=False, known=False, owner=None)])
    assert change.current.status == PresenceStatus.NOBODY_HOME
