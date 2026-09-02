"""Tests for the dot-matching subsystem: search parsing, Oracle prompts,
and the full engine run."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest

from app.config.settings import Settings
from app.dots import DotEnqueueError, DotResearcher, DotsEngine
from app.dots.search import (
    parse_ddg_results,
    parse_github_issue_results,
    parse_github_repo_results,
    parse_hn_results,
)
from app.models.dots import DotRun, DotRunStatus, DotWatch
from app.models.event import HermesEvent

# ------------------------------------------------------------------ parsing


DDG_HTML = """
<div class="result">
  <h2 class="result__title">
    <a class="result__a" rel="nofollow"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpost&amp;rut=abc">Example Post</a>
  </h2>
  <a class="result__snippet">Some snippet of the example page</a>
</div>
<div class="result">
  <h2 class="result__title">
    <a class="result__a" href="https://other.example.org/thing">Other Thing</a>
  </h2>
  <div class="result__snippet"><a href="#">nested</a> second snippet</div>
</div>
"""


def test_parse_ddg_results() -> None:
    hits = parse_ddg_results(DDG_HTML)
    assert len(hits) == 2
    assert hits[0].url == "https://example.com/post"
    assert hits[0].title == "Example Post"
    assert hits[0].snippet == "Some snippet of the example page"
    assert hits[1].url == "https://other.example.org/thing"
    assert hits[1].provider == "duckduckgo"


def test_parse_hn_results() -> None:
    payload = {
        "hits": [
            {
                "objectID": "42",
                "title": "HN Story",
                "url": "https://hacker.example.com",
                "story_text": "A long story text",
            },
            {"objectID": "43", "title": "No URL story"},
        ]
    }
    hits = parse_hn_results(payload)
    assert len(hits) == 2
    assert hits[0].url == "https://hacker.example.com"
    assert hits[0].provider == "hackernews"
    assert hits[1].url == "https://news.ycombinator.com/item?id=43"


def test_parse_hn_results_bad_data() -> None:
    assert parse_hn_results({}) == []
    assert parse_hn_results({"hits": [{"not": "a dict value"}]}) == []


def test_parse_github_results() -> None:
    repos = {
        "items": [
            {"html_url": "https://github.com/a/b", "full_name": "a/b", "description": "A repo"}
        ]
    }
    issues = {
        "items": [
            {"html_url": "https://github.com/a/b/issues/1", "title": "Bug", "body": "Details"}
        ]
    }
    repo_hits = parse_github_repo_results(repos)
    assert repo_hits[0].url == "https://github.com/a/b"
    assert repo_hits[0].provider == "github"
    issue_hits = parse_github_issue_results(issues)
    assert issue_hits[0].title == "Bug"
    assert "Details" in issue_hits[0].snippet
    assert parse_github_repo_results({}) == []


# ------------------------------------------------------------------- Oracle


class StubOracle:
    """Duck-typed OracleClient returning JSON matched by prompt keywords."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.asked: list[str] = []

    async def ask(self, message: str, *, context: str | None = None) -> str:
        self.asked.append(message)
        for key, payload in self._responses.items():
            if key in message:
                return json.dumps(payload)
        return "{}"


def plan_payload() -> dict[str, Any]:
    return {
        "objective_type": "RESEARCH",
        "complexity": "MEDIUM",
        "goal": "investigate topic",
        "success_criteria": ["know the shape"],
        "research_angles": ["main angle"],
        "initial_queries": ["q1", "q2", "q3"],
    }


def match_payload(batch_ids: list[str]) -> dict[str, Any]:
    first = batch_ids[0] if batch_ids else "none"
    return {
        "kept": [
            {
                "id": first,
                "label": "a relevant dot",
                "relevance": 0.9,
                "reason": "on topic",
            }
        ],
        "connections": [{"from": first, "to": "another", "relation": "related", "confidence": 0.8}],
        "note": "first item is on topic",
    }


@pytest.mark.asyncio
async def test_researcher_prompts_round_trip() -> None:
    oracle = StubOracle(
        {
            "Goal Analyzer": plan_payload(),
            "research investigator": {},
            "verification and synthesis": {
                "summary": "exec summary",
                "reasoning_log": "long narrative",
                "key_findings": [{"finding": "F", "support": "S"}],
            },
        }
    )
    researcher = DotResearcher(oracle)
    plan = await researcher.plan("a topic")
    assert plan["initial_queries"] == ["q1", "q2", "q3"]
    assert plan["goal"] == "investigate topic"

    last = await researcher.synthesise("a topic", [{"label": "dot"}], [])
    assert last["summary"] == "exec summary"
    assert last["reasoning_log"] == "long narrative"
    assert last["key_findings"] == [{"finding": "F", "support": "S"}]


@pytest.mark.asyncio
async def test_researcher_match_filters_unknown_ids() -> None:
    payload = match_payload(["known-id"])
    payload["kept"].append({"id": "ghost-id", "label": "ghost", "relevance": 1.0, "reason": "x"})
    oracle = StubOracle({"research investigator": payload})
    researcher = DotResearcher(oracle)
    result = await researcher.match(
        "topic", [], [{"id": "known-id", "title": "gone", "url": "u", "snippet": "s"}]
    )
    assert len(result["kept"]) == 1
    assert result["kept"][0]["id"] == "known-id"


@pytest.mark.asyncio
async def test_researcher_plan_falls_back_when_unparsable() -> None:
    oracle = StubOracle({})
    researcher = DotResearcher(oracle)
    plan = await researcher.plan("fallback topic")
    assert plan["initial_queries"] == ["fallback topic"]


@pytest.mark.asyncio
async def test_researcher_prompts_never_exceed_oracle_message_cap() -> None:
    oracle = StubOracle({})
    researcher = DotResearcher(oracle)
    big = [{"id": f"id-{i}", "title": "x" * 300, "snippet": "y" * 900} for i in range(30)]
    await researcher.match("topic", [{"label": "z" * 200, "url": "u"}], big)
    await researcher.suggest_queries(
        "topic", [{"label": "z" * 200, "url": "u"}] * 30,
        iteration=2, target=12, count=8, seen=set(),
    )
    await researcher.synthesise(
        "topic",
        [{"label": "z" * 200, "url": "u"}] * 30,
        [{"iteration": i, "note": "n" * 300, "kept_count": 3} for i in range(30)],
    )
    for prompt in oracle.asked:
        assert len(prompt) <= 8000


# ------------------------------------------------------------------ engine


class StubSearch:
    """Duck-typed WebSearchClient with deterministic hits + pages."""

    def __init__(self, hits: list, pages: dict[str, tuple[str, str]]) -> None:
        self._hits = hits
        self._pages = pages
        self.queries: list[str] = []

    async def search_all(self, query: str, providers: list[str]) -> list:
        self.queries.append(query)
        return list(self._hits)

    async def fetch_page(self, url: str) -> tuple[str, str]:
        return self._pages.get(url, ("", ""))


class FakeResearcher:
    """Controlled researcher that keeps every item of each chunk it sees."""

    def __init__(self) -> None:
        self.match_calls: list[list[str]] = []

    async def plan(self, topic: str) -> dict[str, Any]:
        return {
            "goal": topic,
            "success_criteria": ["c"],
            "research_angles": ["angle"],
            "initial_queries": ["start-1", "start-2", "start-3"],
        }

    async def suggest_queries(
        self, topic: str, dots: list, *, iteration: int, target: int, count: int, seen: set
    ) -> list[str]:
        seen.add("dive-query")
        return ["dive-query"]

    async def match(
        self, topic: str, prev_dots: list, new_items: list
    ) -> dict[str, Any]:
        ids = [item["id"] for item in new_items]
        self.match_calls.append(ids)
        return {
            "kept": [
                {"id": item_id, "label": "kept dot", "relevance": 0.9, "reason": "on-topic"}
                for item_id in ids
            ],
            "connections": [],
            "note": f"{len(ids)} items relevant",
        }

    async def synthesise(self, topic: str, dots: list, history: list) -> dict[str, Any]:
        return {
            "summary": "Investigation found the dots.",
            "reasoning_log": "We searched, compared and kept relevant material.",
            "key_findings": [{"finding": "the dot", "support": "first item"}],
        }


@pytest.mark.asyncio
async def test_engine_full_run(repository) -> None:
    settings = Settings(dots_batch_cooldown_seconds=0)
    hits = [
        type("Hit", (), {"url": "https://a.example.com/1", "provider": "hackernews"})(),
        type("Hit", (), {"url": "https://b.example.org/2", "provider": "hackernews"})(),
    ]
    pages = {
        "https://a.example.com/1": ("Alpha Post", "Alpha body text about the topic"),
        "https://b.example.org/2": ("Beta Post", "Beta body text that is noise"),
    }
    engine = DotsEngine(
        settings=settings,
        repository=repository,
        oracle=StubOracle({}),
        search=StubSearch(hits, pages),
        researcher=FakeResearcher(),
        publisher=None,
    )

    run = await engine.enqueue("test topic", iterations=2, providers=["hackernews"])
    assert run.status == DotRunStatus.QUEUED

    completed = await engine.execute(run.dot_run_id)
    assert completed.status == DotRunStatus.COMPLETED
    assert completed.dots_kept == 2
    assert completed.report_id is not None

    stored = await repository.get_dot_run(run.dot_run_id)
    assert stored is not None
    assert stored.status == DotRunStatus.COMPLETED
    assert stored.evidence_count == 2
    assert len(stored.reasoning_log) >= 1
    assert stored.metadata.get("plan", {}).get("goal") == "test topic"
    assert "connections" in stored.metadata
    assert stored.metadata.get("elapsed_seconds") is not None

    report = await repository.get_report(completed.report_id)
    assert report is not None
    assert "test topic" in report.title
    assert len(report.evidence_ids) == 2

    batches, batch_total = await repository.list_dot_batches(dot_run_id=run.dot_run_id)
    assert batch_total == 2  # round 1 matched, round 2 had no new urls

    dots, total = await repository.list_dot_runs()
    assert total == 1
    assert await repository.count_dot_runs(status=DotRunStatus.COMPLETED.value) == 1


@pytest.mark.asyncio
async def test_engine_enqueue_validation(repository) -> None:
    engine = DotsEngine(
        settings=Settings(),
        repository=repository,
        oracle=StubOracle({}),
        search=StubSearch([], {}),
        researcher=FakeResearcher(),
        publisher=None,
    )
    with pytest.raises(DotEnqueueError):
        await engine.enqueue("   ")
    with pytest.raises(DotEnqueueError):
        await engine.enqueue("topic", providers=["unknown-provider"])


# ------------------------------------------------------------- subbatches, etc


async def test_subbatches_split_and_merge(repository) -> None:
    settings = Settings(dots_subbatches=4, dots_batch_cooldown_seconds=0)
    researcher = FakeResearcher()
    engine = DotsEngine(
        settings=settings,
        repository=repository,
        oracle=StubOracle({}),
        search=StubSearch([], {}),
        researcher=researcher,
        publisher=None,
    )
    new_items = [
        {"id": f"item-{i}", "title": f"t{i}", "url": f"u{i}", "snippet": "s"}
        for i in range(5)
    ]
    verdict = await engine._match_subbatches(
        DotRun(topic="t", iterations_target=2), [], new_items
    )
    called = sorted(item_id for call in researcher.match_calls for item_id in call)
    assert called == [f"item-{i}" for i in range(5)]
    assert len(researcher.match_calls) == 4
    assert [item["id"] for item in verdict["kept"]] == [f"item-{i}" for i in range(5)]
    assert "|" in verdict["note"]


async def test_subbatch_failure_does_not_kill_batch(repository) -> None:
    class FlakyResearcher(FakeResearcher):
        async def match(self, topic: str, prev_dots: list, new_items: list) -> dict[str, Any]:
            if len(new_items) == 1:
                raise RuntimeError("oracle chunk boom")
            return await super().match(topic, prev_dots, new_items)

    settings = Settings(dots_subbatches=4, dots_batch_cooldown_seconds=0)
    engine = DotsEngine(
        settings=settings,
        repository=repository,
        oracle=StubOracle({}),
        search=StubSearch([], {}),
        researcher=FlakyResearcher(),
        publisher=None,
    )
    new_items = [
        {"id": f"item-{i}", "title": f"t{i}", "url": f"u{i}", "snippet": "s"}
        for i in range(5)
    ]
    verdict = await engine._match_subbatches(
        DotRun(topic="t", iterations_target=2), [], new_items
    )
    kept_ids = {item["id"] for item in verdict["kept"]}
    assert "item-0" in kept_ids and "item-1" in kept_ids  # the 2-item chunk survived
    assert len(kept_ids) == 2  # failed single-item chunks dropped


async def test_batch_cooldown_gives_oracle_a_break(repository, monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.dots.engine.asyncio.sleep", fake_sleep)
    settings = Settings(dots_batch_cooldown_seconds=30.0, dots_max_run_seconds=60)
    hits = [type("Hit", (), {"url": "https://a.example.com/1", "provider": "hackernews"})()]
    pages = {"https://a.example.com/1": ("Alpha Post", "Alpha body text about the topic")}
    engine = DotsEngine(
        settings=settings,
        repository=repository,
        oracle=StubOracle({}),
        search=StubSearch(hits, pages),
        researcher=FakeResearcher(),
        publisher=None,
    )
    run = await engine.enqueue("cooldown topic", iterations=1, providers=["hackernews"])
    await engine.execute(run.dot_run_id)
    assert sleeps == [30.0]


async def test_time_budget_stops_run(repository) -> None:
    settings = Settings(dots_max_run_seconds=0, dots_batch_cooldown_seconds=0)
    engine = DotsEngine(
        settings=settings,
        repository=repository,
        oracle=StubOracle({}),
        search=StubSearch([], {}),
        researcher=FakeResearcher(),
        publisher=None,
    )
    run = await engine.enqueue("budget topic", iterations=5, providers=["hackernews"])
    completed = await engine.execute(run.dot_run_id)
    assert completed.status == DotRunStatus.COMPLETED
    assert completed.dots_kept == 0
    stored = await repository.get_dot_run(run.dot_run_id)
    assert stored is not None
    assert any(
        "Time budget reached" in entry.get("note", "")
        for entry in stored.reasoning_log
    )


# ------------------------------------------------------------- watch scheduler


class RecordingPublisher:
    """Captures every event handed to it, mirroring EventPublisher.publish."""

    def __init__(self) -> None:
        self.published: list[HermesEvent] = []

    async def publish(self, event: HermesEvent) -> None:
        self.published.append(event)


@pytest.mark.asyncio
async def test_watch_repository_crud_and_due_selection(repository) -> None:
    from datetime import timedelta

    from app.models.base import utcnow
    from app.models.dots import DotWatch

    due_time = utcnow() - timedelta(minutes=5)
    later = utcnow() + timedelta(hours=2)
    a = DotWatch(topic="alpha", interval_hours=24.0, next_run_at=due_time)
    b = DotWatch(topic="beta", interval_hours=1.0, next_run_at=later)
    await repository.save_dot_watch(a)
    await repository.save_dot_watch(b)

    watches = await repository.list_dot_watches()
    assert [watch.topic for watch in watches] == ["alpha", "beta"]

    due = await repository.list_due_dot_watches(utcnow())
    assert [watch.dot_watch_id for watch in due] == [a.dot_watch_id]

    # a disabled watch is never due, and re-scheduling hides alpha again.
    b.enabled = False
    await repository.save_dot_watch(b)
    await repository.mark_dot_watch_queued(
        a.dot_watch_id, utcnow() + timedelta(hours=1)
    )
    assert await repository.list_due_dot_watches(utcnow()) == []

    # completion records the latest run and its dot ids for future diffs.
    await repository.complete_dot_watch(
        a.dot_watch_id,
        run_id=UUID(int=1),
        at=utcnow(),
        dot_ids=["https://x", "https://y"],
    )
    refreshed = await repository.get_dot_watch(a.dot_watch_id)
    assert refreshed is not None
    assert refreshed.last_run_id == UUID(int=1)
    assert refreshed.last_dot_ids == ["https://x", "https://y"]

    assert await repository.delete_dot_watch(b.dot_watch_id)
    assert await repository.get_dot_watch(b.dot_watch_id) is None
    assert not await repository.delete_dot_watch(b.dot_watch_id)


@pytest.mark.asyncio
async def test_engine_watch_run_records_delta_and_publishes(repository) -> None:
    publisher = RecordingPublisher()
    hits = [type("Hit", (), {"url": "https://a.example.com/1", "provider": "hackernews"})()]
    pages = {"https://a.example.com/1": ("Alpha Post", "Alpha body text about the topic")}
    engine = DotsEngine(
        settings=Settings(dots_batch_cooldown_seconds=0),
        repository=repository,
        oracle=StubOracle({}),
        search=StubSearch(hits, pages),
        researcher=FakeResearcher(),
        publisher=publisher,
    )
    watch = DotWatch(topic="watched topic", iterations=1)
    await repository.save_dot_watch(watch)

    run = await engine.enqueue("watched topic", iterations=1, watch_id=str(watch.dot_watch_id))
    assert run.metadata["watch"] == {"watch_id": str(watch.dot_watch_id), "topic": "watched topic"}

    completed = await engine.execute(run.dot_run_id)
    assert completed.status == DotRunStatus.COMPLETED

    refreshed = await repository.get_dot_watch(watch.dot_watch_id)
    assert refreshed is not None
    assert refreshed.last_run_id == run.dot_run_id
    assert len(refreshed.last_dot_ids) == 1
    assert UUID(refreshed.last_dot_ids[0])  # content ids are stored, best for diffs

    updates = [e for e in publisher.published if e.type == "dots_watch_update"]
    assert len(updates) == 1
    payload = updates[0].metadata
    assert payload["watch_id"] == str(watch.dot_watch_id)
    assert payload["dots_new"] == 1
    assert payload["dots_total"] == 1
    assert payload["new_dots"][0]["url"] == "https://a.example.com/1"
    assert "1 new dot" in updates[0].message


@pytest.mark.asyncio
async def test_watch_second_run_reports_no_new_dots(repository) -> None:
    publisher = RecordingPublisher()
    hits = [type("Hit", (), {"url": "https://a.example.com/1", "provider": "hackernews"})()]
    pages = {"https://a.example.com/1": ("Alpha Post", "Alpha body text about the topic")}
    engine = DotsEngine(
        settings=Settings(dots_batch_cooldown_seconds=0),
        repository=repository,
        oracle=StubOracle({}),
        search=StubSearch(hits, pages),
        researcher=FakeResearcher(),
        publisher=publisher,
    )
    watch = DotWatch(topic="watched topic", iterations=1)
    await repository.save_dot_watch(watch)

    first = await engine.enqueue("watched topic", iterations=1, watch_id=str(watch.dot_watch_id))
    await engine.execute(first.dot_run_id)
    second = await engine.enqueue("watched topic", iterations=1, watch_id=str(watch.dot_watch_id))
    await engine.execute(second.dot_run_id)

    updates = [e for e in publisher.published if e.type == "dots_watch_update"]
    assert len(updates) == 2
    assert updates[0].metadata["dots_new"] == 1
    assert updates[1].metadata["dots_new"] == 0  # same content, nothing new


@pytest.mark.asyncio
async def test_runtime_watch_tick_enqueues_due_topic_then_reschedules(repository) -> None:
    from datetime import timedelta
    from types import SimpleNamespace

    from app.core.runtime import ArgusRuntime
    from app.models.base import utcnow
    from app.models.dots import DotWatch

    runtime = ArgusRuntime(Settings())
    runtime.repository = repository
    enqueued: list[dict[str, Any]] = []

    async def fake_enqueue(topic: str, **kwargs: Any) -> DotRun:
        enqueued.append({"topic": topic, "kwargs": kwargs})
        run = DotRun(topic=topic, iterations_target=kwargs.get("iterations", 12))
        await repository.save_dot_run(run)
        return run

    runtime.dots = SimpleNamespace(enqueue=fake_enqueue)

    watch = DotWatch(
        topic="tick topic",
        iterations=3,
        interval_hours=0.5,
        next_run_at=utcnow() - timedelta(hours=1),
    )
    await repository.save_dot_watch(watch)

    await runtime._dots_watch_tick()
    assert len(enqueued) == 1
    assert enqueued[0]["kwargs"]["watch_id"] == str(watch.dot_watch_id)
    assert enqueued[0]["kwargs"]["iterations"] == 3

    refreshed = await repository.get_dot_watch(watch.dot_watch_id)
    assert refreshed is not None
    assert refreshed.next_run_at > utcnow()

    await runtime._dots_watch_tick()  # not due yet -> no second enqueue
    assert len(enqueued) == 1
