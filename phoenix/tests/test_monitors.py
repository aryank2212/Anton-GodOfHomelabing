from __future__ import annotations

import httpx

from app.core.clients import Clients
from app.monitors.cpu import CpuMonitor
from app.monitors.disk import DiskMonitor
from app.monitors.docker import DockerMonitor
from app.monitors.http import HTTPMonitor
from app.monitors.memory import MemoryMonitor
from app.monitors.network import NetworkMonitor
from app.monitors.systemd import SystemdMonitor

from .helpers import FakeDockerClient, FakeSystemdClient, mock_http_transport


async def test_http_monitor_ok() -> None:
    http = httpx.AsyncClient(transport=mock_http_transport(httpx.Response(200, text="hello")))
    monitor = HTTPMonitor("web", {"url": "http://x", "expected_status": 200}, Clients(http=http))
    result = await monitor.check()
    assert result.ok
    assert result.status == "healthy"
    await http.aclose()


async def test_http_monitor_wrong_status() -> None:
    http = httpx.AsyncClient(transport=mock_http_transport(httpx.Response(503)))
    monitor = HTTPMonitor("web", {"url": "http://x", "expected_status": 200}, Clients(http=http))
    result = await monitor.check()
    assert not result.ok
    assert result.status == "bad_status"
    await http.aclose()


async def test_http_monitor_missing_body() -> None:
    http = httpx.AsyncClient(transport=mock_http_transport(httpx.Response(200, text="nope")))
    monitor = HTTPMonitor(
        "web",
        {"url": "http://x", "expected_status": 200, "expected_body": "magic"},
        Clients(http=http),
    )
    result = await monitor.check()
    assert not result.ok
    assert result.status == "bad_body"
    await http.aclose()


def _connect_error() -> httpx.ConnectError:
    return httpx.ConnectError("down")


async def test_http_monitor_unreachable() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(_connect_error()))
    )
    monitor = HTTPMonitor("web", {"url": "http://x"}, Clients(http=http))
    result = await monitor.check()
    assert not result.ok
    assert result.status == "unreachable"
    await http.aclose()


async def test_docker_monitor_running() -> None:
    docker = FakeDockerClient(running=True, health_status="healthy")
    monitor = DockerMonitor("db", {"container": "postgres"}, docker)
    result = await monitor.check()
    assert result.ok
    assert result.status == "running"


async def test_docker_monitor_stopped() -> None:
    docker = FakeDockerClient(running=False)
    monitor = DockerMonitor("db", {"container": "postgres"}, docker)
    result = await monitor.check()
    assert not result.ok
    assert result.status == "stopped"


async def test_docker_monitor_health_requirement() -> None:
    docker = FakeDockerClient(running=True, health_status="starting")
    monitor = DockerMonitor("db", {"container": "postgres", "expected_status": "healthy"}, docker)
    result = await monitor.check()
    assert not result.ok
    assert result.status == "unhealthy"


async def test_systemd_monitor() -> None:
    systemd = FakeSystemdClient(active=True)
    monitor = SystemdMonitor("svc", {"unit": "phoenix.service"}, systemd)
    assert (await monitor.check()).ok

    systemd.active = False
    result = await monitor.check()
    assert not result.ok
    assert result.status == "inactive"


async def test_disk_monitor_thresholds() -> None:
    monitor = DiskMonitor("disk", {"path": "/", "threshold_pct": 100})
    assert (await monitor.check()).ok

    monitor = DiskMonitor("disk", {"path": "/", "threshold_pct": 0})
    result = await monitor.check()
    assert not result.ok
    assert result.status == "full"


async def test_memory_monitor_thresholds() -> None:
    monitor = MemoryMonitor("mem", {"threshold_pct": 100})
    assert (await monitor.check()).ok

    monitor = MemoryMonitor("mem", {"threshold_pct": 0})
    assert not (await monitor.check()).ok


async def test_cpu_monitor_thresholds() -> None:
    monitor = CpuMonitor("cpu", {"threshold_pct": 100, "interval": 0.01})
    assert (await monitor.check()).ok

    monitor = CpuMonitor("cpu", {"threshold_pct": -1, "interval": 0.01})
    assert not (await monitor.check()).ok


async def test_network_monitor_reachable() -> None:
    http = httpx.AsyncClient(transport=mock_http_transport(httpx.Response(200)))
    monitor = NetworkMonitor("net", {"host": "gw", "port": 443}, Clients(http=http))
    result = await monitor.check()
    assert result.ok
    assert result.status == "reachable"
    await http.aclose()
