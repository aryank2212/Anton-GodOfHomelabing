"""Observer parsing helpers and observation-shape tests."""

from __future__ import annotations

import httpx

from app.config.loader import HttpTarget, ObserverSpec
from app.core.clients import Clients
from app.models.observation import Severity
from app.network.vendors import VendorLookup, normalize_mac
from app.observers.http import HTTPObserver
from app.observers.network import parse_ip_neigh, parse_proc_arp
from app.observers.router import RouterObserver
from app.observers.ups import UPSObserver
from app.observers.watcher import WatcherObserver, _device_entries
from app.power.base import PowerMonitorError, PowerStatus
from app.power.ups import parse_upsc, to_status


def clients_with(handler) -> Clients:
    return Clients(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


# --------------------------------------------------------------------------- parsing


def test_parse_proc_arp():
    text = """IP address       HW type     Flags       HW address            Mask     Device
192.168.1.1      0x1         0x2         98:9d:b2:a9:9f:8f     *        br0
192.168.1.99     0x1         0x1         00:00:00:00:00:00     *        br0
192.168.1.77     0x1
"""
    entries = parse_proc_arp(text)
    assert entries == [
        {"ip": "192.168.1.1", "mac": "98:9d:b2:a9:9f:8f", "state": "present", "iface": "br0"}
    ]


def test_parse_ip_neigh():
    text = """192.168.1.1 dev br0 lladdr 98:9d:b2:a9:9f:8f REACHABLE
192.168.1.99 dev br0 FAILED
192.168.1.5 dev br0 lladdr a4:83:e7:12:34:56 STALE
"""
    entries = parse_ip_neigh(text)
    assert entries == [
        {"ip": "192.168.1.1", "mac": "98:9d:b2:a9:9f:8f", "state": "present"},
        {"ip": "192.168.1.5", "mac": "a4:83:e7:12:34:56", "state": "seen"},
    ]


def test_normalize_mac():
    assert normalize_mac("11-22-33-44-55-66") == "11:22:33:44:55:66"
    assert normalize_mac("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("aa:bb:cc:dd:ee") == "aa:bb:cc:dd:ee"


def test_vendor_lookup():
    lookup = VendorLookup()
    assert lookup.lookup("98:9d:b2:a9:9f:8f") == "AVM"
    assert lookup.lookup("a4:83:e7:12:34:56") == "Apple"
    assert lookup.lookup("00:00:00:00:00:00") is None
    assert lookup.lookup(None) is None
    custom = VendorLookup({"AA:BB:CC": "Custom Co"})
    assert custom.lookup("aa:bb:cc:00:00:00") == "Custom Co"


def test_parse_upsc_and_to_status():
    out = "ups.status: OL\nbattery.charge: 100.0\ninput.voltage: 230.5\nbattery.charge.low: 20\n"
    values = parse_upsc(out)
    assert values["battery.charge"] == 100.0
    assert values["input.voltage"] == 230.5
    assert values["battery.charge.low"] == "20"
    assert values["ups.status"] == "OL"
    assert to_status({"ups.status": "OL"}) == PowerStatus.ON_LINE
    assert to_status({"ups.status": "OB"}) == PowerStatus.ON_BATTERY
    assert to_status({"ups.status": "OB LB"}) == PowerStatus.LOW_BATTERY
    assert to_status({}) == PowerStatus.UNKNOWN


def test_device_entries_tolerant():
    assert _device_entries([{"mac": "x"}]) == [{"mac": "x"}]
    assert _device_entries({"devices": [{"mac": "y"}]}) == [{"mac": "y"}]
    assert _device_entries({"mac": "z"}) == [{"mac": "z"}]
    assert _device_entries(None) == []
    assert _device_entries(["junk"]) == []


# --------------------------------------------------------------------------- observers


async def test_http_observer_reports_up():
    def handler(request):
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok"})

    spec = ObserverSpec(targets=[HttpTarget(name="hermes", url="http://hermes/health")])
    observer = HTTPObserver(spec, settings=None, clients=clients_with(handler))
    collected = await observer.collect()
    assert len(collected) == 1
    obs = collected[0]
    assert obs.object == "http:hermes"
    assert obs.state == "up"
    assert obs.severity == Severity.INFO
    assert obs.metadata["status_code"] == 200


async def test_http_observer_reports_down_on_error():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    spec = ObserverSpec(targets=[HttpTarget(name="hermes", url="http://hermes/health")])
    observer = HTTPObserver(spec, settings=None, clients=clients_with(handler))
    collected = await observer.collect()
    obs = collected[0]
    assert obs.state == "down"
    assert obs.severity == Severity.MEDIUM
    assert obs.metadata["status_code"] is None
    assert "error" in obs.metadata


async def test_watcher_observer_online_with_devices():
    def handler(request):
        if request.url.path == "/health":
            return httpx.Response(200)
        return httpx.Response(
            200,
            json=[
                {
                    "mac": "98:9d:b2:a9:9f:8f",
                    "ip": "192.168.1.1",
                    "state": "online",
                }
            ],
        )

    spec = ObserverSpec(params={"base_url": "http://watcher"})
    observer = WatcherObserver(spec, settings=None, clients=clients_with(handler))
    collected = await observer.collect()
    assert collected[0].state == "online"
    device_obs = collected[1]
    assert device_obs.object == "device:98:9d:b2:a9:9f:8f"
    assert device_obs.metadata["vendor"] == "AVM"


async def test_watcher_observer_offline_when_api_down():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    spec = ObserverSpec(params={"base_url": "http://watcher"})
    observer = WatcherObserver(spec, settings=None, clients=clients_with(handler))
    collected = await observer.collect()
    assert len(collected) == 1
    assert collected[0].state == "offline"
    assert collected[0].severity == Severity.HIGH


async def test_router_tcp_check_failure_on_closed_port():
    spec = ObserverSpec(params={"method": "tcp", "host": "127.0.0.1", "port": 65500})
    observer = RouterObserver(spec, settings=None, clients=None)
    ok, error = await observer._tcp_check("127.0.0.1", 65500)
    assert ok is False
    assert error


class _FakeMonitor:
    available = True

    def __init__(self, error: bool = False) -> None:
        self.error = error

    async def read(self):
        if self.error:
            raise PowerMonitorError("boom")
        return None


async def test_ups_observer_empty_when_unavailable():
    observer = UPSObserver(ObserverSpec(), settings=None, clients=None)
    observer.monitor = _FakeMonitor()
    observer.monitor.available = False
    assert await observer.collect() == []


async def test_ups_observer_unavailable_observation_on_error():
    observer = UPSObserver(ObserverSpec(), settings=None, clients=None)
    observer.monitor = _FakeMonitor(error=True)
    collected = await observer.collect()
    assert len(collected) == 1
    assert collected[0].object == "ups"
    assert collected[0].state == "unavailable"
