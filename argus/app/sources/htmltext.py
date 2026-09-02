"""Shared HTML helpers for collectors and the dot-matching engine.

Scrape collectors and the dots engine both turn raw HTML pages into the
human-readable title + body that become a ContentItem. Extraction lives here
once instead of being duplicated across both code paths.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class _TextExtractor(HTMLParser):
    """Pulls human-readable text out of a page, ignoring scripts/styles."""

    _BLOCK = {"script", "style", "noscript", "template", "svg", "head"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._BLOCK:
            self._skip_depth += 1
        elif tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "br", "section"):
            self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("br", "hr"):
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK and self._skip_depth:
            self._skip_depth -= 1
        elif tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "section"):
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self.parts.append(data)


def extract_title(html: str, fallback: str) -> str:
    """Best-effort page title, falling back to a caller-provided label."""
    match = _TITLE.search(html)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else fallback


def extract_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - broken HTML must never crash a collect
        return ""
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()
