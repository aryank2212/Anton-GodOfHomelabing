from __future__ import annotations

from typing import ClassVar

import httpx

from app.providers.base import BaseProvider, ProviderError, ProviderMessage

_NTFY_PRIORITY = {
    "debug": 1,
    "info": 2,
    "warning": 3,
    "error": 4,
    "critical": 5,
}

_NTFY_TAG = {
    "debug": "mag",
    "info": "white_check_mark",
    "warning": "warning",
    "error": "octagonal_sign",
    "critical": "rotating_light",
}


class NtfyProvider(BaseProvider):
    name = "ntfy"
    templates: ClassVar[dict[str, str]] = {"text": "ntfy.j2"}

    @property
    def enabled(self) -> bool:
        return bool(self.settings.ntfy_topic)

    async def send(self, message: ProviderMessage) -> None:
        topic = self.settings.ntfy_topic
        if not topic:
            raise ProviderError("ntfy topic is not configured")

        base_url = self.settings.ntfy_url.rstrip("/")
        url = f"{base_url}/{topic}"
        severity = str(message.event.get("severity", "info"))
        payload = {
            "title": message.event.get("title", ""),
            "message": message.rendered.get("text", ""),
            "priority": _NTFY_PRIORITY.get(severity, 2),
            "tags": [_NTFY_TAG.get(severity, "white_check_mark")],
        }
        headers = {}
        if self.settings.ntfy_token:
            headers["Authorization"] = f"Bearer {self.settings.ntfy_token}"

        try:
            response = await self._client.post(url, json=payload, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"ntfy request failed: {exc}") from exc
