"""Research tools — web_search via injected fake, fetch/write_note on mocked httpx."""

from __future__ import annotations

import httpx
import pytest

from app.tools.research_tools import FetchUrl, WebSearch, WriteNote


def _fetch_client(body: str, status: int = 200) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, text=body))
    )


FAKE_RESULTS = [
    {"title": "First Result", "url": "https://example.com/a", "snippet": "Alpha snippet"},
    {"title": "Second Result", "url": "https://example.com/b", "snippet": "Beta snippet"},
]


def _fake_search(query: str, max_results: int) -> list[dict[str, str]]:
    return FAKE_RESULTS[:max_results]


async def test_web_search_returns_formatted_results() -> None:
    result = await WebSearch(search_fn=_fake_search).run({"query": "penguins"})
    assert result.ok
    assert result.data["results"][0]["title"] == "First Result"
    assert "example.com/a" in result.output
    assert "Alpha snippet" in result.output


async def test_web_search_respects_max_results() -> None:
    result = await WebSearch(search_fn=_fake_search).run({"query": "penguins", "max_results": 1})
    assert len(result.data["results"]) == 1


async def test_web_search_requires_query() -> None:
    result = await WebSearch(search_fn=_fake_search).run({"query": "  "})
    assert result.ok is False
    assert "query is required" in result.error


async def test_web_search_surfaces_backend_error() -> None:
    def boom(query: str, max_results: int) -> list[dict[str, str]]:
        raise RuntimeError("rate limited")

    result = await WebSearch(search_fn=boom).run({"query": "penguins"})
    assert result.ok is False
    assert "rate limited" in result.error


async def test_web_search_empty_is_ok() -> None:
    result = await WebSearch(search_fn=lambda q, m: []).run({"query": "penguins"})
    assert result.ok
    assert result.data["results"] == []


async def test_fetch_url_strips_tags_and_caps() -> None:
    body = (
        "<html><head><title>My Page</title></head>"
        "<body><h1>Hello</h1><p>Some text.</p></body></html>"
    )
    client = _fetch_client(body)
    result = await FetchUrl(client=client).run({"url": "https://example.com"})
    await client.aclose()
    assert result.ok
    assert "title: My Page" in result.output
    assert "Hello" in result.output


async def test_fetch_url_rejects_non_http() -> None:
    result = await FetchUrl(client=_fetch_client("x")).run({"url": "file:///etc/passwd"})
    assert result.ok is False
    assert "http" in result.error


async def test_fetch_url_reports_bad_status() -> None:
    client = _fetch_client("nope", status=404)
    result = await FetchUrl(client=client).run({"url": "https://example.com/missing"})
    await client.aclose()
    assert result.ok is False


@pytest.fixture
def notes_dir(tmp_path):
    return tmp_path / "notes"


async def test_write_note_appends_with_timestamp(notes_dir) -> None:
    tool = WriteNote(notes_dir)
    result = await tool.run({"filename": "ideas.txt", "content": "build a synth"})
    assert result.ok
    assert "ideas.txt" in result.output
    content = (notes_dir / "ideas.txt").read_text()
    assert "build a synth" in content
    assert "UTC" in content

    await tool.run({"filename": "ideas.txt", "content": "second idea"})
    assert (notes_dir / "ideas.txt").read_text().count("second idea") == 1


async def test_write_note_rejects_path_traversal(notes_dir) -> None:
    tool = WriteNote(notes_dir)
    assert not (await tool.run({"filename": "../escape.txt", "content": "x"})).ok
    assert not (await tool.run({"filename": "sub/dir.txt", "content": "x"})).ok
    assert not (await tool.run({"filename": "noext", "content": "x"})).ok


async def test_write_note_requires_content(notes_dir) -> None:
    tool = WriteNote(notes_dir)
    assert not (await tool.run({"filename": "a.txt", "content": "  "})).ok


async def test_write_note_identity_uses_filename(notes_dir) -> None:
    tool = WriteNote(notes_dir)
    assert tool.identity({"filename": "a.txt"}) == "a.txt"
