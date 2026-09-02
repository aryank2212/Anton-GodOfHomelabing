from __future__ import annotations

from typing import ClassVar

import httpx

from app.providers.base import BaseProvider, ProviderError, ProviderMessage


class TelegramProvider(BaseProvider):
    name = "telegram"
    templates: ClassVar[dict[str, str]] = {"text": "telegram.j2"}

    @property
    def enabled(self) -> bool:
        return bool(self.settings.telegram_bot_token and self.settings.telegram_chat_id)

    async def send(self, message: ProviderMessage) -> None:
        token = self.settings.telegram_bot_token
        chat_id = self.settings.telegram_chat_id
        if not token or not chat_id:
            raise ProviderError("telegram bot token or chat id is not configured")

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message.rendered.get("text", ""),
            "disable_web_page_preview": True,
        }
        try:
            response = await self._client.post(url, json=payload)
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"telegram request failed: {exc}") from exc
        if not data.get("ok"):
            raise ProviderError(f"telegram api error: {data}")
