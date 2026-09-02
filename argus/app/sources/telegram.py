"""Telegram collector — public channel mirror scraping.

For each channel in ``params.channels`` the public web preview at
``https://t.me/s/<channel>`` is scraped; each message becomes one ContentItem.
This is a skeleton implementation: no MTProto, no bot tokens — just the
public HTML preview, which is enough to follow infosec/social signals.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import httpx

from app.config.loader import SourceSpec
from app.config.settings import Settings
from app.core.clients import Clients
from app.core.logging import get_logger
from app.models.content import ContentItem, SourceType
from app.sources.base import Collector

log = get_logger(__name__)

_MESSAGE_LINK = re.compile(r'<a class="tgme_widget_message_link[^"]*"[^>]*href="([^"]+)"')
_MESSAGE_TEXT = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.DOTALL
)
_HTML_TAG = re.compile(r"<[^>]+>")


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", _HTML_TAG.sub(" ", value)).strip()


class TelegramCollector(Collector):
    name = "telegram"
    source_type = SourceType.TELEGRAM
    description = "Public Telegram channels (web preview)"

    def __init__(self, spec: SourceSpec, settings: Settings, clients: Clients) -> None:
        super().__init__(spec, default_interval=600.0, default_timeout=30.0)
        self.channels: list[dict] = list(spec.params.get("channels") or [])
        if clients.http is None:
            raise RuntimeError("telegram collector requires a shared httpx client")
        self._client = clients.http

    async def collect(self) -> Sequence[ContentItem]:
        items: list[ContentItem] = []
        for channel in self.channels:
            name = str(channel.get("name", "channel"))
            handle = str(channel.get("channel", "")).strip().lstrip("@")
            if not handle:
                continue
            try:
                items.extend(await self._collect_channel(name, handle))
            except httpx.HTTPError as exc:
                log.warning(
                    "telegram_fetch_failed",
                    extra={"channel": handle, "error": f"{type(exc).__name__}: {exc}"},
                )
        return items

    async def _collect_channel(self, name: str, handle: str) -> list[ContentItem]:
        response = await self._client.get(f"https://t.me/s/{handle}")
        response.raise_for_status()
        html = response.text
        links = _MESSAGE_LINK.findall(html)
        texts = [_strip_html(block) for block in _MESSAGE_TEXT.findall(html)]

        items: list[ContentItem] = []
        for index, text in enumerate(texts):
            if not text:
                continue
            url = links[index] if index < len(links) else None
            items.append(
                self._item(
                    url=url,
                    title=f"{name}: {text[:120]}",
                    body=text,
                    metadata={"channel": handle, "channel_name": name},
                    tags=["telegram", name],
                )
            )
        return items


def build_telegram_collector(spec: SourceSpec, settings: Settings, clients: Clients) -> Collector:
    return TelegramCollector(spec, settings, clients)
