"""Configuration loading tests against the bundled YAML files."""

from __future__ import annotations

from app.config.loader import (
    load_devices,
    load_observers_config,
    load_vendors,
    load_yaml,
)
from app.correlation.loader import load_rules
from app.correlation.rules import Condition, Rule
from tests.conftest import CONFIG_DIR


def test_rules_file_loads_all_rules():
    rules = load_rules(CONFIG_DIR / "rules.yaml")
    assert len(rules) == 11
    ids = {rule.id for rule in rules}
    assert {
        "router_offline",
        "internet_offline",
        "power_outage",
        "ups_on_battery",
        "ups_low_battery",
        "watcher_offline",
        "hermes_offline",
        "container_unhealthy",
        "unknown_device_joined",
        "unknown_device_present",
        "resource_pressure",
    } <= ids


def test_rules_are_valid_types_and_enabled():
    rules = load_rules(CONFIG_DIR / "rules.yaml")
    assert all(rule.type in {"boolean", "absence", "count"} for rule in rules)
    assert all(rule.enabled for rule in rules)


def test_absence_rules_match_healthy_state():
    """Absence rules must fire when the healthy signal disappears."""
    rules = {rule.id: rule for rule in load_rules(CONFIG_DIR / "rules.yaml")}
    assert "online" in rules["watcher_offline"].match.all[0].states
    assert "up" in rules["hermes_offline"].match.all[0].states


def test_condition_window_normalization():
    rule = Rule(
        id="r",
        name="R",
        window_seconds=120,
        match={"all": [{"source": "router", "state": "offline"}]},
    )
    rule.normalize_condition_windows()
    assert rule.match.all[0].window_seconds == 120


def test_condition_scalar_normalized_to_list():
    condition = Condition(source="router", state="offline")
    assert condition.sources == ["router"]
    assert condition.states == ["offline"]


def test_devices_file():
    devices = load_devices(CONFIG_DIR / "devices.yaml").devices
    by_name = {device.name: device for device in devices}
    router = by_name.get("Router")
    assert router is not None
    assert router.mac == "98:9d:b2:a9:9f:8f"
    assert by_name["Oracle Laptop"].owner == "Aryan"


def test_vendors_file_keys_normalized():
    vendors = load_vendors(CONFIG_DIR / "vendors.yaml")
    assert all(":" in prefix and prefix == prefix.lower() for prefix in vendors)


def test_observers_config():
    config = load_observers_config(CONFIG_DIR / "observers.yaml")
    assert {"system", "docker", "router", "watcher", "ups", "http", "network"} <= set(
        config.observers
    )
    network = config.observers["network"]
    assert network.interval == 60.0
    http_targets = {target.name for target in config.observers["http"].targets}
    assert {"hermes", "internet"} <= http_targets


def test_missing_yaml_returns_empty():
    assert load_yaml(CONFIG_DIR / "does-not-exist.yaml") == {}
