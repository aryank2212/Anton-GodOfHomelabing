"""HTTP endpoint monitor."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.clients import Clients, ClientUnavailableError
from app.models.check import MonitorResult
from app.monitors.base import Monitor, safe_check


class HTTPMonitor(Monitor):
    """Checks that an HTTP endpoint responds as expected.

    ``params``:
      url:              endpoint to request (required)
      method:           HTTP method (default ``GET``)
      expected_status:  expected HTTP status (default 200)
      expected_body:    optional substring that must appear in the body
      timeout:          request timeout in seconds (default 5)
    """

    kind = "http"

    def __init__(self, name: str, params: dict[str, Any], clients: Clients) -> None:
        super().__init__(name, params)
        self._client = clients.http
        self.url = str(params["url"])
        self.method = str(params.get("method", "GET")).upper()
        self.expected_status = int(params.get("expected_status", 200))
        self.expected_body = str(params.get("expected_body", "")) or None
        self.timeout = float(params.get("timeout", 5))

    @safe_check
    async def check(self) -> MonitorResult:
        if self._client is None:
            return MonitorResult.failing("unavailable", "no http client available")
        try:
            response = await self._client.request(self.method, self.url, timeout=self.timeout)
        except httpx.HTTPError as exc:
            return MonitorResult.failing(
                "unreachable",
                f"{self.method} {self.url}: {type(exc).__name__}: {exc}",
                url=self.url,
            )

        if response.status_code != self.expected_status:
            return MonitorResult.failing(
                "bad_status",
                f"{self.method} {self.url} returned {response.status_code}, "
                f"expected {self.expected_status}",
                url=self.url,
                status_code=response.status_code,
                expected_status=self.expected_status,
            )

        if self.expected_body and self.expected_body not in response.text:
            return MonitorResult.failing(
                "bad_body",
                f"{self.method} {self.url} response is missing expected text",
                url=self.url,
            )

        return MonitorResult.healthy("healthy", url=self.url, status_code=response.status_code)


def build_http_monitor(name: str, params: dict[str, Any], clients: Clients) -> Monitor:
    if clients.http is None:
        raise ClientUnavailableError("no http client available")
    return HTTPMonitor(name, params, clients)
