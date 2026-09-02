"""HTTP observer — periodic health checks of configured endpoints.

Every target in ``observers.yaml`` is probed each cycle. Reachability becomes
an ``up``/``down`` observation. Targets like Hermes or an external host feed
the ``hermes_offline`` / ``internet_offline`` correlation rules.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

import httpx

from app.config.loader import HttpTarget, ObserverSpec
from app.config.settings import Settings
from app.core.clients import Clients
from app.core.logging import get_logger
from app.models.observation import Category, Observation, Severity
from app.observers.base import Observer

log = get_logger(__name__)


class HTTPObserver(Observer):
    name = "http"
    category = Category.INFRASTRUCTURE
    description = "Periodic health checks of configured endpoints"

    def __init__(self, spec: ObserverSpec, settings: Settings, clients: Clients) -> None:
        super().__init__(spec, default_interval=60.0, default_timeout=10.0)
        self.targets: list[HttpTarget] = list(spec.targets)
        client = clients.http
        assert client is not None, "http observer requires a shared httpx client"
        self._client = client

    async def collect(self) -> Sequence[Observation]:
        observations: list[Observation] = []
        for target in self.targets:
            observations.append(await self._check_target(target))
        return observations

    async def _check_target(self, target: HttpTarget) -> Observation:
        started = time.perf_counter()
        try:
            response = await self._client.get(target.url, timeout=target.timeout)
            ok = response.status_code == target.expected_status
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            status_code = response.status_code
            error = None
        except httpx.HTTPError as exc:
            ok = False
            latency_ms = None
            status_code = None
            error = f"{type(exc).__name__}: {exc}"

        return self._observation(
            object=f"http:{target.name}",
            state="up" if ok else "down",
            severity=Severity.INFO if ok else Severity.MEDIUM,
            confidence=0.95 if ok else 0.8,
            metadata={
                "name": target.name,
                "url": target.url,
                "status_code": status_code,
                "latency_ms": latency_ms,
                "error": error,
            },
            tags=["http", target.name],
        )


def build_http_observer(spec: ObserverSpec, settings: Settings, clients: Clients) -> Observer:
    return HTTPObserver(spec, settings, clients)
