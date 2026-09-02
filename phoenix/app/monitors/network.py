"""Network reachability monitor."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.clients import Clients, ClientUnavailableError
from app.models.check import MonitorResult
from app.monitors.base import Monitor, safe_check


class NetworkMonitor(Monitor):
    """Fails when a remote host/port cannot be reached over TCP.

    ``params``:
      host:     host to reach, e.g. ``192.168.1.1`` (required)
      port:     TCP port to connect to (default 443)
      timeout:  connection timeout in seconds (default 5)
    """

    kind = "network"

    def __init__(self, name: str, params: dict[str, Any], clients: Clients) -> None:
        super().__init__(name, params)
        self._client = clients.http
        self.host = str(params["host"])
        self.port = int(params.get("port", 443))
        self.timeout = float(params.get("timeout", 5))

    @safe_check
    async def check(self) -> MonitorResult:
        if self._client is None:
            return MonitorResult.failing("unavailable", "no http client available")
        scheme = "https" if self.port == 443 else "http"
        url = f"{scheme}://{self.host}:{self.port}/"
        try:
            response = await self._client.request("GET", url, timeout=self.timeout)
        except httpx.HTTPError as exc:
            return MonitorResult.failing(
                "unreachable",
                f"{self.host}:{self.port} unreachable: {type(exc).__name__}: {exc}",
                host=self.host,
                port=self.port,
            )
        return MonitorResult.healthy(
            "reachable",
            host=self.host,
            port=self.port,
            status_code=response.status_code,
        )


def build_network_monitor(name: str, params: dict[str, Any], clients: Clients) -> Monitor:
    if clients.http is None:
        raise ClientUnavailableError("no http client available")
    return NetworkMonitor(name, params, clients)
