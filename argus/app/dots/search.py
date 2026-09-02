"""Web search + page fetching for dot-matching runs.

The server-side half of a dot run: turn queries into candidate URLs across
several free providers and fetch the pages as human-readable text. No API keys
required — DuckDuckGo's HTML results, Hacker News' Algolia index and GitHub's
search API are all public. Only *fetching* happens here; the reasoning that
decides what is a relevant dot is always delegated to Oracle.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

import httpx

from app.core.logging import get_logger
from app.sources.htmltext import extract_text, extract_title

log = get_logger(__name__)

DDG_URL = "https://html.duckduckgo.com/html/"
HN_URL = "https://hn.algolia.com/api/v1/search"
GITHUB_REPO_URL = "https://api.github.com/search/repositories"
GITHUB_ISSUE_URL = "https://api.github.com/search/issues"


@dataclass(frozen=True)
class SearchHit:
    """A single search-engine result before any page is fetched."""

    url: str
    title: str
    snippet: str = ""
    provider: str = ""


class _DDGParser(HTMLParser):
    """Extracts title + destination-url + snippet from DuckDuckGo's HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hits: list[SearchHit] = []
        self._in_result = False
        self._in_snippet = False
        self._snip_tag = ""
        self._buf: list[str] = []
        self._href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = set((dict(attrs).get("class") or "").split())
        if tag == "a" and {"result__a", "result-link"} & classes:
            self._in_result = True
            self._buf = []
            self._href = _resolve_ddg_href(dict(attrs).get("href") or "")
        elif "result__snippet" in classes:
            self._in_snippet = True
            self._snip_tag = tag
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._in_result or self._in_snippet:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_result:
            self.hits.append(
                SearchHit(
                    url=self._href,
                    title=re.sub(r"\s+", " ", "".join(self._buf)).strip(),
                    provider="duckduckgo",
                )
            )
            self._in_result = False
            self._buf = []
        elif self._in_snippet and tag == self._snip_tag:
            if self.hits:
                snippet = re.sub(r"\s+", " ", "".join(self._buf)).strip()
                self.hits[-1] = SearchHit(
                    url=self.hits[-1].url,
                    title=self.hits[-1].title,
                    snippet=snippet,
                    provider=self.hits[-1].provider,
                )
            self._in_snippet = False
            self._buf = []


def _resolve_ddg_href(href: str) -> str:
    """DuckDuckGo wraps real URLs in /l/?uddg=<encoded>. Unwrap it."""
    if href.startswith("//"):
        href = "https:" + href
    query = parse_qs(urlparse(href).query)
    target = query.get("uddg")
    if target:
        return target[0]
    return href


def parse_ddg_results(html: str) -> list[SearchHit]:
    parser = _DDGParser()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - broken HTML must never crash a round
        return []
    return [hit for hit in parser.hits if hit.url]


def parse_hn_results(data: dict) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for item in data.get("hits") or []:
        if not isinstance(item, dict):
            continue
        object_id = item.get("objectID")
        url = item.get("url")
        if not url and object_id:
            url = f"https://news.ycombinator.com/item?id={object_id}"
        title = (item.get("title") or "").strip()
        if not url or not title:
            continue
        snippet = (item.get("story_text") or "").strip()
        hits.append(
            SearchHit(url=url, title=title[:512], snippet=snippet[:500], provider="hackernews")
        )
    return hits


def parse_github_repo_results(data: dict) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        url = item.get("html_url")
        title = (item.get("full_name") or "").strip()
        if not url or not title:
            continue
        hits.append(
            SearchHit(
                url=url,
                title=title[:512],
                snippet=(item.get("description") or "").strip()[:500],
                provider="github",
            )
        )
    return hits


def parse_github_issue_results(data: dict) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        url = item.get("html_url")
        title = (item.get("title") or "").strip()
        if not url or not title:
            continue
        body = (item.get("body") or "").strip()
        hits.append(SearchHit(url=url, title=title[:512], snippet=body[:500], provider="github"))
    return hits


_PROVIDER_SET = {"duckduckgo", "hackernews", "github"}
_GITHUB_MIN_INTERVAL = 8.0  # unauthenticated GitHub search API allows 10/min


class WebSearchClient:
    """Queries registered providers and fetches individual pages."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        timeout: float = 12.0,
        hits_per_provider: dict[str, int] | None = None,
    ) -> None:
        self._client = client
        self._timeout = timeout
        self._hits_per_provider = hits_per_provider or {
            "duckduckgo": 6,
            "hackernews": 8,
            "github": 6,
        }
        self._last_call: dict[str, float] = {}

    async def search(self, query: str, provider: str) -> list[SearchHit]:
        if provider not in _PROVIDER_SET:
            return []
        await self._pace(provider)
        try:
            if provider == "duckduckgo":
                return await self._search_ddg(query)
            if provider == "hackernews":
                return await self._search_hn(query)
            return await self._search_github(query)
        except httpx.HTTPError as exc:
            log.warning(
                "dots_search_failed",
                extra={
                    "provider": provider,
                    "query": query,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return []

    async def search_all(self, query: str, providers: list[str]) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for provider in providers:
            hits.extend(await self.search(query, provider))
        return hits

    async def fetch_page(self, url: str) -> tuple[str, str]:
        """Fetch one page, returning (title, extracted text)."""
        try:
            response = await self._client.get(url, timeout=self._timeout)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "html" not in content_type:
                return "", ""
            html = response.text
            fallback = url.split("/")[-1] or url
            return extract_title(html, fallback), extract_text(html)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning(
                "dots_fetch_failed",
                extra={"url": url, "error": f"{type(exc).__name__}: {exc}"},
            )
            return "", ""

    # ------------------------------------------------------------ internals
    async def _pace(self, provider: str) -> None:
        min_interval = _GITHUB_MIN_INTERVAL if provider == "github" else 0.0
        last = self._last_call.get(provider)
        if last is not None:
            wait = (last + min_interval) - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
        self._last_call[provider] = time.monotonic()

    async def _search_ddg(self, query: str) -> list[SearchHit]:
        response = await self._client.get(
            DDG_URL, params={"q": query}, timeout=self._timeout
        )
        response.raise_for_status()
        return parse_ddg_results(response.text)[: self._hits_per_provider["duckduckgo"]]

    async def _search_hn(self, query: str) -> list[SearchHit]:
        response = await self._client.get(
            HN_URL,
            params={"query": query, "hitsPerPage": self._hits_per_provider["hackernews"]},
            timeout=self._timeout,
        )
        response.raise_for_status()
        return parse_hn_results(response.json())

    async def _search_github(self, query: str) -> list[SearchHit]:
        headers = {"Accept": "application/vnd.github+json"}
        repo_response = await self._client.get(
            GITHUB_REPO_URL,
            params={"q": query, "per_page": 5},
            headers=headers,
            timeout=self._timeout,
        )
        repo_response.raise_for_status()
        hits = parse_github_repo_results(repo_response.json())
        try:
            issue_response = await self._client.get(
                GITHUB_ISSUE_URL,
                params={"q": query, "per_page": 5},
                headers=headers,
                timeout=self._timeout,
            )
            issue_response.raise_for_status()
            hits.extend(parse_github_issue_results(issue_response.json()))
        except httpx.HTTPError as exc:
            log.warning("dots_github_issues_failed", extra={"error": str(exc)})
        return hits[: self._hits_per_provider["github"]]
