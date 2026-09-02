"""Research tools: DuckDuckGo search, URL fetch, and audited note saving.

These give the agent "research hands": look something up on the web, read a
page, and persist findings as timestamped .txt notes inside a managed folder.
Notes are path-locked to ``notes_dir`` and every write goes through the normal
policy + Level-1 approval flow like any other act tool.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from pathlib import Path

import httpx

from app.tools.base import Tool, ToolResult, ToolSpec

_USER_AGENT = "Mozilla/5.0 (Anton Forge research agent; contact: local homelab)"
_STRIP_TAGS = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


class WebSearch(Tool):
    """Free DuckDuckGo search via the ``ddgs`` library (no API key).

    Plain httpx requests get TLS-fingerprinted and challenged from some IPs;
    ddgs randomizes ciphers and HTTP/2 settings to get through, so we use it
    behind an injectable ``search_fn`` seam for tests.
    """

    def __init__(
        self,
        max_results: int = 5,
        timeout: int = 15,
        search_fn: Callable[[str, int], list[dict[str, str]]] | None = None,
    ) -> None:
        self._max_results = max_results
        self._timeout = timeout
        self._search_fn = search_fn

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="web_search",
            description=(
                "Search the web via DuckDuckGo. Returns up to max_results "
                "results as title + URL + snippet. Use fetch_url to read a "
                "page in full. Use this to gather background info about a "
                "person, technology, error message or current events."
            ),
            risk="low",
            read_only=True,
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "search query", "minLength": 1},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "how many results to return (default 5)",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        )

    def _ddgs_search(self, query: str, max_results: int) -> list[dict[str, str]]:
        from ddgs import DDGS

        with DDGS(timeout=self._timeout) as ddgs:
            items = ddgs.text(query, max_results=max_results)
        return [
            {
                "title": str(item.get("title") or "").strip(),
                "url": str(item.get("href") or "").strip(),
                "snippet": str(item.get("body") or "").strip(),
            }
            for item in items
        ]

    async def run(self, args: dict) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(ok=False, error="query is required")
        max_results = max(1, min(int(args.get("max_results") or self._max_results), 10))
        try:
            if self._search_fn is not None:
                results = self._search_fn(query, max_results)
            else:
                results = await asyncio.to_thread(self._ddgs_search, query, max_results)
        except Exception as exc:  # ddgs raises various exceptions; surface them
            return ToolResult(ok=False, error=f"search failed: {exc}")
        if not results:
            return ToolResult(ok=True, output="no results", data={"results": []})
        lines = [
            f"{i + 1}. {r['title']}\n   {r['url']}\n   {r['snippet']}"
            for i, r in enumerate(results)
        ]
        return ToolResult(ok=True, output="\n".join(lines), data={"results": results})


class FetchUrl(Tool):
    """Fetch a page and return its readable text (tags stripped, size-capped)."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        max_bytes: int = 50_000,
        timeout: float = 20.0,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": _USER_AGENT}
        )
        self._max_bytes = max_bytes

    def owned_clients(self) -> list[httpx.AsyncClient]:
        return [self._client] if self._owns_client else []

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="fetch_url",
            description=(
                "Fetch a URL (http/https) and return its readable text with "
                "tags stripped. Capped at ~50KB. Use after web_search to read "
                "a promising result in full."
            ),
            risk="low",
            read_only=True,
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "absolute http(s) URL",
                        "pattern": "^https?://",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        )

    async def run(self, args: dict) -> ToolResult:
        url = str(args.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return ToolResult(ok=False, error="url must start with http:// or https://")
        try:
            response = await self._client.get(url, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return ToolResult(ok=False, error=f"fetch failed: {exc}")
        raw = response.text[: self._max_bytes]
        title = ""
        title_match = re.search(r"<title[^>]*>(.*?)</title>", raw, flags=re.S)
        if title_match:
            title = _WHITESPACE.sub(" ", _STRIP_TAGS.sub("", title_match.group(1))).strip()
        body = _WHITESPACE.sub(" ", _STRIP_TAGS.sub(" ", raw)).strip()
        output = f"title: {title}\nurl: {url}\n{body[:4000]}"
        return ToolResult(ok=True, output=output, data={"url": url, "title": title})


class WriteNote(Tool):
    """Append a timestamped note to a .txt file inside the managed notes dir."""

    _FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.txt$")

    def __init__(self, notes_dir: str | Path) -> None:
        self._notes_dir = Path(notes_dir)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="write_note",
            description=(
                "Append a timestamped note to a .txt file inside the managed "
                "notes folder. Use this to save research findings, facts or "
                "tasks so they persist. Filename must be a simple name ending "
                "in .txt; content is appended (never overwrites)."
            ),
            risk="medium",
            read_only=False,
            parameters={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "simple .txt filename, e.g. research.md",
                        "pattern": self._FILENAME.pattern,
                    },
                    "content": {
                        "type": "string",
                        "description": "note body to append",
                        "minLength": 1,
                        "maxLength": 20_000,
                    },
                },
                "required": ["filename", "content"],
                "additionalProperties": False,
            },
        )

    def identity(self, args: dict) -> str:
        return str(args.get("filename") or "?")

    async def run(self, args: dict) -> ToolResult:
        filename = str(args.get("filename") or "")
        content = str(args.get("content") or "")
        if not self._FILENAME.match(filename):
            return ToolResult(
                ok=False,
                error=f"filename must match {self._FILENAME.pattern}",
            )
        if not content.strip():
            return ToolResult(ok=False, error="content is required")
        notes_dir = self._notes_dir.resolve()
        notes_dir.mkdir(parents=True, exist_ok=True)
        path = (notes_dir / filename).resolve()
        if not path.is_relative_to(notes_dir):
            return ToolResult(ok=False, error="filename escapes the notes directory")
        if path.exists() and not path.is_file():
            return ToolResult(ok=False, error="target is not a regular file")
        from datetime import UTC, datetime

        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n[{timestamp}]\n{content.strip()}\n")
        return ToolResult(
            ok=True,
            output=f"appended note ({len(content)} chars) to {filename}",
            data={"path": str(path), "chars": len(content)},
        )
