from __future__ import annotations

from types import SimpleNamespace

from app.config.loader import SourceSpec
from app.config.settings import Settings
from app.core.clients import Clients
from app.sources.rss import RSSCollector, _strip_html


def test_strip_html() -> None:
    assert _strip_html("<p>Hello <b>world</b>!</p>") == "Hello world !"


def test_entry_to_item() -> None:
    collector = RSSCollector(SourceSpec(), Settings(), Clients.defaults())
    entry = SimpleNamespace(
        link="https://example.com/post/1",
        title="Breaking",
        summary="<p>Some body</p>",
        published="Thu, 01 Jan 2026 00:00:00 GMT",
        tags=[SimpleNamespace(term="cve")],
        feed=SimpleNamespace(title="Feed Title"),
    )
    item = collector._entry_to_item("https://feed.example/rss.xml", entry)
    assert item is not None
    assert item.title == "Breaking"
    assert item.body == "Some body"
    assert item.url == "https://example.com/post/1"
    assert item.source_type.value == "rss"
    assert item.metadata["feed"] == "https://feed.example/rss.xml"
    assert item.metadata["feed_title"] == "Feed Title"
    assert item.metadata["tags"] == ["cve"]
    assert item.content_hash
