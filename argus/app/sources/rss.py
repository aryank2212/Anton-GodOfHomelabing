"""RSS / Atom collector — the news and security feed source.

Each feed listed in ``params.feeds`` is fetched and parsed; every entry becomes
one ContentItem. ``content_hash`` is derived from the entry URL so re-fetches
dedupe even when the feed reorders or edits entries.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import feedparser
import httpx

from app.config.loader import SourceSpec
from app.config.settings import Settings
from app.core.clients import Clients
from app.core.logging import get_logger
from app.models.content import ContentItem, SourceType
from app.sources.base import Collector

log = get_logger(__name__)

_LANG = re.compile(r'<html[^>]*lang="([^"]+)"', re.IGNORECASE)


class RSSCollector(Collector):
    name = "rss"
    source_type = SourceType.RSS
    description = "News, blogs and security feeds"

    def __init__(self, spec: SourceSpec, settings: Settings, clients: Clients) -> None:
        super().__init__(spec, default_interval=900.0, default_timeout=30.0)
        self.feeds: list[str] = list(spec.params.get("feeds") or [])
        if clients.http is None:
            raise RuntimeError("rss collector requires a shared httpx client")
        self._client = clients.http

    async def collect(self) -> Sequence[ContentItem]:
        items: list[ContentItem] = []
        for feed_url in self.feeds:
            async for item in self._collect_feed(feed_url):
                items.append(item)
        return items

    async def _collect_feed(self, feed_url: str):
        try:
            response = await self._client.get(feed_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning(
                "rss_fetch_failed",
                extra={"feed": feed_url, "error": f"{type(exc).__name__}: {exc}"},
            )
            return
        parsed = feedparser.parse(response.content)
        for entry in parsed.entries:
            item = self._entry_to_item(feed_url, entry)
            if item is not None:
                yield item

    def _entry_to_item(self, feed_url: str, entry) -> ContentItem | None:
        link = getattr(entry, "link", None)
        title = getattr(entry, "title", "") or ""
        if not title:
            return None
        summary = getattr(entry, "summary", "") or ""
        body = _strip_html(summary).strip()
        published = getattr(entry, "published", "") or getattr(entry, "updated", "") or ""
        feed_title = getattr(entry.feed, "title", "") if getattr(entry, "feed", None) else ""
        return self._item(
            url=link or None,
            title=title,
            body=body,
            metadata={
                "feed": feed_url,
                "feed_title": feed_title,
                "published": published,
                "tags": [tag.term for tag in getattr(entry, "tags", [])],
            },
            tags=["rss", feed_title or feed_url],
        )


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", text).strip()


def build_rss_collector(spec: SourceSpec, settings: Settings, clients: Clients) -> Collector:
    return RSSCollector(spec, settings, clients)
