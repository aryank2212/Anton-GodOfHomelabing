"""OSINT collector — enrichment APIs.

Each provider in ``params.providers`` needs its API key present in the
environment (``api_key_env``) before it will be called; providers without a
key are skipped and logged. The generic mapping is deliberately simple for the
skeleton: a JSON list is located via a dotted ``items_path`` and each entry is
projected onto ``title_path`` / ``body_path`` / ``url_path``.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

import httpx

from app.config.loader import SourceSpec
from app.config.settings import Settings
from app.core.clients import Clients
from app.core.logging import get_logger
from app.models.content import ContentItem, SourceType
from app.sources.base import Collector

log = get_logger(__name__)


def _dig(data: Any, path: str) -> Any:
    """Follow a dotted path into nested dicts/lists, or return None."""
    if not path or path == ".":
        return data
    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


class OSINTCollector(Collector):
    name = "osint"
    source_type = SourceType.OSINT
    description = "OSINT / threat-intel enrichment APIs"

    def __init__(self, spec: SourceSpec, settings: Settings, clients: Clients) -> None:
        super().__init__(spec, default_interval=3600.0, default_timeout=30.0)
        self.providers: list[dict] = list(spec.params.get("providers") or [])
        if clients.http is None:
            raise RuntimeError("osint collector requires a shared httpx client")
        self._client = clients.http

    async def collect(self) -> Sequence[ContentItem]:
        items: list[ContentItem] = []
        for provider in self.providers:
            async for item in self._collect_provider(provider):
                items.append(item)
        return items

    async def _collect_provider(self, provider: dict):
        name = str(provider.get("name", "provider"))
        api_key_env = str(provider.get("api_key_env", ""))
        api_key = os.getenv(api_key_env) if api_key_env else None
        if not api_key:
            log.debug(
                "osint_provider_skipped",
                extra={"provider": name, "missing_key": api_key_env},
            )
            return
        url = str(provider.get("url", ""))
        if not url:
            return
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            response = await self._client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning(
                "osint_fetch_failed",
                extra={"provider": name, "url": url, "error": f"{type(exc).__name__}: {exc}"},
            )
            return
        entries = _dig(payload, str(provider.get("items_path", ".")))
        if not isinstance(entries, list):
            return
        title_path = str(provider.get("title_path", "title"))
        body_path = str(provider.get("body_path", "body"))
        url_path = str(provider.get("url_path", "url"))
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            item = self._entry_to_item(name, entry, title_path, body_path, url_path)
            if item is not None:
                yield item

    def _entry_to_item(
        self,
        provider: str,
        entry: dict,
        title_path: str,
        body_path: str,
        url_path: str,
    ) -> ContentItem | None:
        title = _dig(entry, title_path)
        if title is None:
            return None
        body = _dig(entry, body_path)
        item_url = _dig(entry, url_path)
        return self._item(
            url=str(item_url) if item_url else None,
            title=str(title),
            body=str(body) if body else "",
            metadata={"provider": provider, "entry": entry},
            tags=["osint", provider],
        )


def build_osint_collector(spec: SourceSpec, settings: Settings, clients: Clients) -> Collector:
    return OSINTCollector(spec, settings, clients)
