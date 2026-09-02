"""Scrape collector — tracked websites and change detection.

Each target in ``params.targets`` is fetched on every cycle. The normalized
text becomes the item body; the ``content_hash`` fingerprint is computed over
that normalized body, so an identical re-fetch dedupes away while a real page
change produces a new item with the same URL — that is exactly what the change
detector keys on.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import httpx

from app.config.loader import SourceSpec
from app.config.settings import Settings
from app.core.clients import Clients
from app.core.logging import get_logger
from app.models.content import ContentItem, SourceType
from app.sources.base import Collector
from app.sources.htmltext import extract_text, extract_title

log = get_logger(__name__)


class ScrapeCollector(Collector):
    name = "scrape"
    source_type = SourceType.SCRAPE
    description = "Tracked websites monitored for change"

    def __init__(self, spec: SourceSpec, settings: Settings, clients: Clients) -> None:
        super().__init__(spec, default_interval=1800.0, default_timeout=30.0)
        self.targets: list[dict] = list(spec.params.get("targets") or [])
        if clients.http is None:
            raise RuntimeError("scrape collector requires a shared httpx client")
        self._client = clients.http

    async def collect(self) -> Sequence[ContentItem]:
        items: list[ContentItem] = []
        for target in self.targets:
            name = str(target.get("name", "target"))
            url = str(target.get("url", ""))
            if not url:
                continue
            try:
                item = await self._fetch_target(name, url)
            except httpx.HTTPError as exc:
                log.warning(
                    "scrape_fetch_failed",
                    extra={"target": name, "url": url, "error": f"{type(exc).__name__}: {exc}"},
                )
                continue
            if item is not None:
                items.append(item)
        return items

    async def _fetch_target(self, name: str, url: str) -> ContentItem | None:
        response = await self._client.get(url)
        response.raise_for_status()
        html = response.text
        title = extract_title(html, name)
        body = extract_text(html)
        return self._item(
            url=url,
            title=title[:512] or name,
            body=body,
            language=None,
            metadata={
                "target": name,
                "html_hash": hashlib.sha256(html.encode("utf-8")).hexdigest(),
                "html_length": len(html),
                "status_code": response.status_code,
            },
            tags=["scrape", name],
        )


def build_scrape_collector(spec: SourceSpec, settings: Settings, clients: Clients) -> Collector:
    return ScrapeCollector(spec, settings, clients)
