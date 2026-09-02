from __future__ import annotations

import json
from typing import ClassVar

import httpx

from app.providers.base import BaseProvider, ProviderError, ProviderMessage


class WebhookProvider(BaseProvider):
    """Generic JSON webhook. Useful for forwarding events to arbitrary
    endpoints (e.g. a future automation service)."""

    name = "webhook"
    templates: ClassVar[dict[str, str]] = {"payload": "webhook.j2"}

    @property
    def enabled(self) -> bool:
        return bool(self.settings.webhook_url)

    async def send(self, message: ProviderMessage) -> None:
        url = self.settings.webhook_url
        if not url:
            raise ProviderError("webhook url is not configured")

        raw = message.rendered.get("payload", "{}")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"invalid webhook payload: {exc}") from exc

        headers = {"Content-Type": "application/json"}
        if self.settings.webhook_secret:
            headers["Authorization"] = f"Bearer {self.settings.webhook_secret}"

        try:
            response = await self._client.post(url, json=payload, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"webhook request failed: {exc}") from exc
