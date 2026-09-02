"""Event publisher — Argus' only outbound channel.

Argus publishes standardized events to Hermes' ``POST /event`` endpoint. It
never contacts Discord, Telegram or any other notification provider directly.
When Hermes is disabled or unreachable, events are dropped after a few retries
and logged — ingestion must never block on notification.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

import httpx

from app.config.settings import Settings
from app.core.logging import get_logger
from app.models.event import HermesEvent

log = get_logger(__name__)


class EventPublisher(Protocol):
    """What the runtime needs from an event publisher."""

    async def publish(self, event: HermesEvent) -> bool: ...

    async def aclose(self) -> None: ...


class HermesPublisher:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._enabled = settings.hermes_enabled
        self._url = settings.hermes_event_url
        self._timeout = settings.hermes_timeout
        self._attempts = max(1, settings.hermes_retry_attempts + 1)
        self._backoff = settings.hermes_retry_backoff
        self._client = client or httpx.AsyncClient(timeout=self._timeout)

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def publish(self, event: HermesEvent) -> bool:
        if not self._enabled:
            log.debug("event_publish_disabled", extra={"type": event.type})
            return True
        last_error: Exception | None = None
        for attempt in range(1, self._attempts + 1):
            try:
                response = await self._client.post(self._url, json=event.model_dump(mode="json"))
                if response.status_code < 300:
                    log.info(
                        "event_published",
                        extra={
                            "type": event.type,
                            "severity": event.severity,
                            "correlation_id": str(event.correlation_id),
                            "attempt": attempt,
                        },
                    )
                    return True
                last_error = httpx.HTTPStatusError(
                    f"hermes returned {response.status_code}",
                    request=response.request,
                    response=response,
                )
            except httpx.HTTPError as exc:
                last_error = exc
            log.warning(
                "event_publish_failed",
                extra={"type": event.type, "attempt": attempt, "error": str(last_error)},
            )
            if attempt < self._attempts:
                await asyncio.sleep(self._backoff)
        return False

    async def aclose(self) -> None:
        await self._client.aclose()


class NullPublisher:
    """No-op publisher used when Hermes integration is disabled."""

    enabled = False

    async def publish(self, event: HermesEvent) -> bool:  # pragma: no cover - trivial
        log.debug("event_publish_disabled", extra={"type": event.type})
        return True

    async def aclose(self) -> None:  # pragma: no cover - trivial
        return None
