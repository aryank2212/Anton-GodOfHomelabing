"""Router observer — gateway reachability.

Checks the default gateway with ICMP ping (or a TCP connect). The gateway is
auto-detected from ``/proc/net/route`` unless ``host`` is set in YAML.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import Sequence

from app.config.loader import ObserverSpec
from app.config.settings import Settings
from app.core.clients import Clients
from app.core.logging import get_logger
from app.models.observation import Category, Observation, Severity
from app.observers.base import Observer

log = get_logger(__name__)


def detect_gateway() -> str:
    """Return the default IPv4 gateway from /proc/net/route (empty if none)."""
    try:
        with open("/proc/net/route", encoding="ascii") as handle:
            for line in handle.readlines()[1:]:
                fields = line.strip().split()
                if len(fields) >= 3 and fields[1] == "00000000":
                    hex_ip = fields[2]
                    octets = [str(int(hex_ip[i : i + 2], 16)) for i in range(6, -2, -2)]
                    return ".".join(octets)
    except OSError:
        return ""
    return ""


class RouterObserver(Observer):
    name = "router"
    category = Category.NETWORK
    description = "Default gateway reachability"

    def __init__(self, spec: ObserverSpec, settings: Settings, clients: Clients) -> None:
        super().__init__(spec, default_interval=15.0, default_timeout=10.0)
        params = spec.params
        self.method = str(params.get("method", "ping"))
        self.host = str(params.get("host", "") or detect_gateway())
        self.port = int(params.get("port", 80))
        self.count = int(params.get("count", 1))
        self.deadline = float(params.get("deadline", 2))

    async def collect(self) -> Sequence[Observation]:
        host = self.host
        if not host:
            log.warning("router_no_gateway")
            return []

        if self.method == "tcp":
            ok, error = await self._tcp_check(host, self.port)
        else:
            if shutil.which("ping") is None:
                log.warning("router_ping_missing")
                return []
            ok, error = await self._ping_check(host)

        return [
            self._observation(
                object="gateway",
                state="online" if ok else "offline",
                severity=Severity.INFO if ok else Severity.HIGH,
                confidence=0.95 if ok else 0.85,
                metadata={
                    "host": host,
                    "method": self.method,
                    "error": error,
                },
                tags=["router", "gateway"],
            )
        ]

    async def _ping_check(self, host: str) -> tuple[bool, str]:
        cmd = ["ping", "-c", str(self.count), "-W", str(self.deadline), host]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=self.timeout)
            return proc.returncode == 0, ""
        except (TimeoutError, OSError) as exc:
            return False, f"{type(exc).__name__}: {exc}"

    async def _tcp_check(self, host: str, port: int) -> tuple[bool, str]:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=self.timeout
            )
            writer.close()
            await writer.wait_closed()
            return True, ""
        except (TimeoutError, OSError) as exc:
            return False, f"{type(exc).__name__}: {exc}"


def build_router_observer(spec: ObserverSpec, settings: Settings, clients: Clients) -> Observer:
    return RouterObserver(spec, settings, clients)
