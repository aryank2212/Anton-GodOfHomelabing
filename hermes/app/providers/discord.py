from __future__ import annotations

from typing import ClassVar

import httpx

from app.providers.base import BaseProvider, ProviderError, ProviderMessage


class DiscordProvider(BaseProvider):
    name = "discord"
    templates: ClassVar[dict[str, str]] = {"text": "discord.j2"}

    @property
    def enabled(self) -> bool:
        return bool(self.settings.discord_webhook_url)

    async def send(self, message: ProviderMessage) -> None:
        url = self.settings.discord_webhook_url
        if not url:
            raise ProviderError("discord webhook url is not configured")
        content = message.rendered.get("text", "")[:2000]
        try:
            response = await self._client.post(url, json={"username": "Hermes", "content": content})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"discord request failed: {exc}") from exc
