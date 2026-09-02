"""Retry a failed HTTP service."""

from __future__ import annotations

from typing import Any

import httpx

from app.recovery.base import RecoveryError, RecoveryResult, RecoveryStrategy


class HTTPRetryStrategy(RecoveryStrategy):
    """Probe an HTTP endpoint again; a healthy response is a successful
    recovery. The retry policy engine provides the attempt schedule.

    ``params``:
      url:              endpoint to probe (required)
      method:           HTTP method (default ``GET``)
      expected_status:  expected status (default 200)
      timeout:          request timeout in seconds (default 5)
    """

    name = "http_retry"

    def __init__(self, params: dict[str, Any], clients: Any) -> None:
        super().__init__(params, clients)
        self.url = str(params["url"])
        self.method = str(params.get("method", "GET")).upper()
        self.expected_status = int(params.get("expected_status", 200))
        self.timeout = float(params.get("timeout", 5))

    async def execute(self) -> RecoveryResult:
        client = self.clients.http
        if client is None:
            raise RecoveryError("no http client available")
        try:
            response = await client.request(self.method, self.url, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise RecoveryError(f"{self.method} {self.url}: {type(exc).__name__}") from exc
        if response.status_code != self.expected_status:
            raise RecoveryError(
                f"{self.method} {self.url} returned {response.status_code}, "
                f"expected {self.expected_status}"
            )
        return RecoveryResult.ok(
            self.name,
            f"{self.method} {self.url} responded {response.status_code}",
            url=self.url,
            status_code=response.status_code,
        )
