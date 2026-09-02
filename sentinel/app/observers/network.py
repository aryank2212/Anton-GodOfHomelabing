"""Network observer — neighbour discovery on the LAN.

Reads the local ARP table (``/proc/net/arp``) or ``ip neigh`` and reports every
device currently on the network. These observations drive the device tracker
(inventory + presence). Parsing helpers are pure functions for testability.
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
from app.network.vendors import VendorLookup
from app.observers.base import Observer

log = get_logger(__name__)


def parse_proc_arp(text: str) -> list[dict[str, str]]:
    """Parse ``/proc/net/arp`` into ``{ip, mac, state}`` entries."""
    entries: list[dict[str, str]] = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 6:
            continue
        ip, _hw_type, flags = fields[0], fields[1], fields[2]
        mac, _mask, device = fields[3], fields[4], fields[5]
        if mac and mac != "00:00:00:00:00:00":
            entries.append(
                {
                    "ip": ip,
                    "mac": mac,
                    "state": "present" if flags == "0x2" else "seen",
                    "iface": device,
                }
            )
    return entries


def parse_ip_neigh(text: str) -> list[dict[str, str]]:
    """Parse ``ip neigh`` output into ``{ip, mac, state}`` entries."""
    entries: list[dict[str, str]] = []
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        ip = fields[0]
        mac = ""
        state = "seen"
        for index, token in enumerate(fields):
            if token == "lladdr" and index + 1 < len(fields):
                mac = fields[index + 1]
            if token in {"REACHABLE", "PERMANENT"}:
                state = "present"
        if mac:
            entries.append({"ip": ip, "mac": mac, "state": state})
    return entries


class NetworkObserver(Observer):
    name = "network"
    category = Category.NETWORK
    description = "Neighbour discovery on the local network"

    def __init__(self, spec: ObserverSpec, settings: Settings, clients: Clients) -> None:
        super().__init__(spec, default_interval=60.0, default_timeout=10.0)
        self.method = str(spec.params.get("method", "arp"))
        self._vendors = VendorLookup()

    async def collect(self) -> Sequence[Observation]:
        if self.method == "ip":
            entries = await self._read_ip_neigh()
        else:
            entries = await asyncio.to_thread(self._read_proc_arp)

        observations: list[Observation] = []
        for entry in entries:
            mac = entry.get("mac", "")
            ip = entry.get("ip", "")
            state = entry.get("state", "seen")
            observations.append(
                self._observation(
                    object=f"device:{mac or ip}",
                    state=state,
                    severity=Severity.INFO,
                    confidence=0.9 if state == "present" else 0.7,
                    metadata={
                        "mac": mac,
                        "ip": ip,
                        "vendor": self._vendors.lookup(mac) if mac else None,
                        "iface": entry.get("iface", ""),
                    },
                    tags=["network", "arp"],
                )
            )
        return observations

    def _read_proc_arp(self) -> list[dict[str, str]]:
        try:
            with open("/proc/net/arp", encoding="ascii") as handle:
                return parse_proc_arp(handle.read())
        except OSError as exc:  # pragma: no cover - /proc always exists on Linux
            log.warning("network_proc_arp_failed", extra={"error": str(exc)})
            return []

    async def _read_ip_neigh(self) -> list[dict[str, str]]:
        if shutil.which("ip") is None:
            log.warning("network_ip_missing")
            return []
        try:
            proc = await asyncio.create_subprocess_exec(
                "ip",
                "neigh",
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
            return parse_ip_neigh(stdout.decode(errors="replace"))
        except (TimeoutError, OSError) as exc:
            log.warning("network_ip_neigh_failed", extra={"error": str(exc)})
            return []


def build_network_observer(spec: ObserverSpec, settings: Settings, clients: Clients) -> Observer:
    return NetworkObserver(spec, settings, clients)
