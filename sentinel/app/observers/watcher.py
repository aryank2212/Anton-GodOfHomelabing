"""Watcher observer — consumes Watcher / WatchYourLAN device intelligence.

Watcher is Anton's environmental awareness service. Sentinel treats it as one
more data source: it polls Watcher's API, converts reachability into a
``watcher`` observation (which feeds the ``watcher_offline`` rule) and
re-emits any device records it finds as device observations.

The exact Watcher API schema is still evolving, so parsing is deliberately
tolerant: lists and object wrappers are both accepted, unknown shapes are
skipped, and failures simply become an "offline" observation.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from app.config.loader import ObserverSpec
from app.config.settings import Settings
from app.core.clients import Clients
from app.core.logging import get_logger
from app.models.observation import Category, Observation, Severity
from app.network.vendors import VendorLookup
from app.observers.base import Observer

log = get_logger(__name__)


def _device_entries(payload: Any) -> list[dict[str, Any]]:
    """Tolerantly extract a list of device dicts from a Watcher payload."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("devices", "items", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [payload]
    return []


class WatcherObserver(Observer):
    name = "watcher"
    category = Category.NETWORK
    description = "Watcher / WatchYourLAN device intelligence"

    def __init__(self, spec: ObserverSpec, settings: Settings, clients: Clients) -> None:
        super().__init__(spec, default_interval=20.0, default_timeout=10.0)
        params = spec.params
        self.base_url = str(params.get("base_url", "http://127.0.0.1:8008")).rstrip("/")
        self.devices_path = str(params.get("devices_path", "/devices"))
        client = clients.http
        assert client is not None, "watcher observer requires a shared httpx client"
        self._client = client
        self._vendors = VendorLookup()

    async def collect(self) -> Sequence[Observation]:
        observations: list[Observation] = []
        try:
            response = await self._client.get(f"{self.base_url}/health")
            reachable = response.status_code < 400
        except httpx.HTTPError:
            reachable = False

        observations.append(
            self._observation(
                object="watcher",
                state="online" if reachable else "offline",
                severity=Severity.INFO if reachable else Severity.HIGH,
                confidence=0.95 if reachable else 0.85,
                metadata={"base_url": self.base_url},
                tags=["watcher"],
            )
        )

        if reachable:
            observations.extend(await self._ingest_devices())
        return observations

    async def _ingest_devices(self) -> list[Observation]:
        try:
            response = await self._client.get(f"{self.base_url}{self.devices_path}")
            if response.status_code != 200:
                return []
            entries = _device_entries(response.json())
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            log.debug("watcher_devices_parse_failed", extra={"error": str(exc)})
            return []

        observations: list[Observation] = []
        for entry in entries:
            mac = entry.get("mac") or entry.get("hw_address")
            ip = entry.get("ip") or entry.get("ip_address")
            hostname = entry.get("hostname") or entry.get("host_name") or entry.get("name")
            state_value = str(entry.get("state", entry.get("status", "present")))
            online = state_value.lower() not in ("offline", "disconnected", "left", "down")
            observations.append(
                self._observation(
                    object=f"device:{mac or ip or hostname or 'unknown'}",
                    state="present" if online else "seen",
                    severity=Severity.INFO,
                    confidence=0.9,
                    metadata={
                        "mac": mac,
                        "ip": ip,
                        "hostname": hostname,
                        "vendor": self._vendors.lookup(mac) if mac else None,
                    },
                    tags=["watcher", "device"],
                )
            )
        return observations


def build_watcher_observer(spec: ObserverSpec, settings: Settings, clients: Clients) -> Observer:
    return WatcherObserver(spec, settings, clients)
