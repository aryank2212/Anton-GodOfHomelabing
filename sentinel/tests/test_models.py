"""Model contract tests: enums, defaults, immutability, lifecycle."""

from __future__ import annotations

from datetime import UTC

import pytest
from pydantic import ValidationError

from app.models.device import (
    PRESENCE_LABELS,
    Device,
    DeviceEvent,
    PresenceState,
    PresenceStatus,
)
from app.models.event import HERMES_SEVERITY, MODULE, HermesEvent
from app.models.observation import Category, Observation, Severity, utcnow
from app.models.situation import Situation, SituationStatus


def test_utcnow_is_timezone_aware():
    assert utcnow().tzinfo is not None
    assert utcnow().utcoffset() == UTC.utcoffset(None)


def test_observation_defaults():
    obs = Observation(source="router", category=Category.NETWORK, object="gateway", state="online")
    assert obs.severity == Severity.INFO
    assert obs.confidence == 1.0
    assert obs.tags == []
    assert obs.metadata == {}


def test_observation_is_immutable():
    obs = Observation(source="router", category=Category.NETWORK, object="gateway", state="online")
    with pytest.raises(ValidationError):
        obs.confidence = 0.5


def test_observation_confidence_bounds():
    with pytest.raises(ValidationError):
        Observation(source="r", category=Category.NETWORK, object="g", state="o", confidence=1.5)
    with pytest.raises(ValidationError):
        Observation(source="r", category=Category.NETWORK, object="g", state="o", confidence=-0.1)


def test_observation_with_tags_returns_copy():
    obs = Observation(source="router", category=Category.NETWORK, object="gateway", state="online")
    tagged = obs.with_tags("b", "a")
    assert tagged.tags == ["a", "b"]
    assert obs.tags == []


def test_observation_required_strings():
    with pytest.raises(ValidationError):
        Observation(category=Category.NETWORK, object="g", state="o")


def test_situation_lifecycle():
    situation = Situation(rule_id="power_outage", type="power_outage", name="Power Outage")
    assert situation.active
    assert situation.status == SituationStatus.ACTIVE
    resolved = situation.resolve(summary="resolved now")
    assert not resolved.active
    assert resolved.status == SituationStatus.RESOLVED
    assert resolved.resolved_at is not None
    assert situation.active  # original unchanged (immutable copy)


def test_situation_record_round_trip():
    situation = Situation(rule_id="r1", type="r1", name="Rule One")
    record = situation.to_record_dict()
    assert record["rule_id"] == "r1"
    assert isinstance(record["derived_from"], list)
    assert record["status"] == "active"


def test_device_display_name_precedence():
    assert Device(device_key="k", name="Phone", hostnames=["h"]).display_name == "Phone"
    assert Device(device_key="k", hostnames=["host"]).display_name == "host"
    assert Device(device_key="k", mac="aa:bb").display_name == "aa:bb"
    assert Device(device_key="ip:1.2.3.4").display_name == "ip:1.2.3.4"


def test_device_event_pattern():
    DeviceEvent(device_key="k", event="joined")
    DeviceEvent(device_key="k", event="left")
    DeviceEvent(device_key="k", event="seen")
    with pytest.raises(ValidationError):
        DeviceEvent(device_key="k", event="wobbled")


def test_presence_state_label():
    state = PresenceState(status=PresenceStatus.HOME_OCCUPIED)
    assert state.label == PRESENCE_LABELS[PresenceStatus.HOME_OCCUPIED]


def test_hermes_event_defaults():
    event = HermesEvent(type="presence_change", title="Home Occupied")
    assert event.module == MODULE
    assert event.severity == "info"
    assert event.correlation_id is not None
    with pytest.raises(ValidationError):
        HermesEvent(type="x", title="y", severity="loud")


def test_hermes_severity_mapping():
    assert HERMES_SEVERITY["critical"] == "critical"
    assert HERMES_SEVERITY["high"] == "error"
    assert HERMES_SEVERITY["medium"] == "warning"
    assert HERMES_SEVERITY["low"] == "info"
    assert HERMES_SEVERITY["info"] == "info"


def test_category_and_severity_enums():
    assert {c.value for c in Category} == {
        "network",
        "presence",
        "power",
        "environment",
        "infrastructure",
        "security",
        "system",
    }
    assert {s.value for s in Severity} == {"info", "low", "medium", "high", "critical"}
