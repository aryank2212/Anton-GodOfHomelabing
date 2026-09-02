"""Dots engine — orchestrates one internet investigation end to end.

A dot run is executed entirely by this engine:

1. plan    — Oracle turns the topic into a research plan (Infer goal
             analyzer / planner).
2. loop    — repeatedly: search the web -> fetch pages as evidence ->
             Oracle matches the fresh batch against the dots already kept ->
             keep the relevant ones, note the reasoning, go deeper.
3. write   — Oracle synthesises the findings and a long-form reasoning log;
             the engine persists an intelligence report and hands it to Hermes.

The server never reasons: it only searches, fetches and stores. All LLM work
is Oracle calls over the tailnet.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import UUID

from app.config.settings import Settings
from app.core.logging import get_logger
from app.core.oracle import OracleClient
from app.core.publisher import EventPublisher
from app.database.repository import Repository
from app.dots.researcher import DotResearcher
from app.dots.search import SearchHit, WebSearchClient
from app.models.base import utcnow
from app.models.content import ContentItem, SourceType, content_hash
from app.models.dots import DotBatch, DotRun, DotRunStatus
from app.models.event import (
    EVENT_DOTS_WATCH_UPDATE,
    EVENT_REPORT_PUBLISHED,
    HermesEvent,
)
from app.models.report import Report, ReportStatus

log = get_logger(__name__)

VALID_PROVIDERS = ("duckduckgo", "hackernews", "github")


def _chunk_items(
    items: list[dict[str, Any]], n: int
) -> list[list[dict[str, Any]]]:
    """Split a batch into at most ``n`` even chunks (larger chunks first)."""
    total = len(items)
    if total == 0:
        return []
    k = max(1, min(n, total))
    base, extra = divmod(total, k)
    chunks: list[list[dict[str, Any]]] = []
    idx = 0
    for i in range(k):
        size = base + (1 if i < extra else 0)
        chunks.append(items[idx : idx + size])
        idx += size
    return chunks


def _watch_summary(topic: str, new_count: int, total: int) -> str:
    if new_count == 0:
        return f"No new dots for “{topic}” — {total} dots tracked so far."
    noun = "dot" if new_count == 1 else "dots"
    return f"{new_count} new {noun} in “{topic}” ({total} now tracked)."


class DotEnqueueError(ValueError):
    """Raised when a dot run is queued with invalid parameters."""


class DotsEngine:
    """Serial executor for dot-matching investigations."""

    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        oracle: OracleClient,
        search: WebSearchClient,
        researcher: DotResearcher,
        publisher: EventPublisher | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._oracle = oracle
        self._search = search
        self._researcher = researcher
        self._publisher = publisher

    async def enqueue(
        self,
        topic: str,
        *,
        iterations: int | None = None,
        providers: list[str] | None = None,
        queries_per_round: int | None = None,
        max_items_per_round: int | None = None,
        watch_id: str | None = None,
        session_id: str | None = None,
    ) -> DotRun:
        topic = topic.strip()
        if not topic:
            raise DotEnqueueError("topic must not be empty")
        provider_list = providers or list(VALID_PROVIDERS)
        unknown = [name for name in provider_list if name not in VALID_PROVIDERS]
        if unknown:
            raise DotEnqueueError(f"unknown providers: {', '.join(unknown)}")
        run = DotRun(
            topic=topic,
            iterations_target=iterations or self._settings.dots_default_iterations,
            providers=provider_list,
            queries_per_round=(
                queries_per_round or self._settings.dots_queries_per_round
            ),
            max_items_per_round=(
                max_items_per_round or self._settings.dots_max_items_per_round
            ),
        )
        if watch_id:
            run.metadata["watch"] = {"watch_id": watch_id, "topic": topic}
        if session_id:
            run.session_id = UUID(session_id)
        await self._repository.save_dot_run(run)
        return run

    async def execute(self, dot_run_id: UUID) -> DotRun:
        """Run a queued investigation to completion. Never raises."""
        run = await self._repository.get_dot_run(dot_run_id)
        if run is None:
            raise ValueError(f"dot run {dot_run_id} not found")
        run.status = DotRunStatus.RUNNING
        await self._repository.save_dot_run(run)
        try:
            return await self._run_investigation(run)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a failed run must not kill the worker
            log.error("dots_run_failed", extra={"run": dot_run_id, "error": str(exc)})
            run = await self._repository.get_dot_run(dot_run_id)
            assert run is not None
            run.status = DotRunStatus.FAILED
            run.error = str(exc)[:4096]
            await self._repository.save_dot_run(run)
            return run

    # ------------------------------------------------------------ internals
    async def _run_investigation(self, run: DotRun) -> DotRun:
        repository = self._repository
        started_at = time.monotonic()
        time_budget = self._settings.dots_max_run_seconds

        plan = await self._researcher.plan(run.topic)
        run.metadata["plan"] = plan
        run.metadata["connections"] = []

        dots: list[dict[str, Any]] = []
        history: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        seen_queries: set[str] = {
            value.lower() for value in plan.get("initial_queries") or []
        }
        seen_hashes: set[str] = set()
        queries: list[str] = list(plan.get("initial_queries") or [run.topic])

        for iteration in range(1, run.iterations_target + 1):
            fresh = await repository.get_dot_run(run.dot_run_id)
            assert fresh is not None
            if fresh.status == DotRunStatus.CANCELLED:
                run.status = DotRunStatus.CANCELLED
                await repository.save_dot_run(run)
                return run

            if time_budget >= 0 and time.monotonic() - started_at >= time_budget:
                history.append(
                    {
                        "iteration": iteration,
                        "queries": [],
                        "note": "Time budget reached — investigation stopped.",
                        "kept_count": 0,
                    }
                )
                break

            if iteration > 1:
                queries = await self._researcher.suggest_queries(
                    run.topic,
                    dots,
                    iteration=iteration,
                    target=run.iterations_target,
                    count=run.queries_per_round,
                    seen=seen_queries,
                )
            if not queries:
                history.append(
                    {
                        "iteration": iteration,
                        "queries": [],
                        "note": "No new queries suggested — investigation converged.",
                        "kept_count": 0,
                    }
                )
                break

            hits = await self._gather_hits(run, queries, seen_urls)

            items = await self._fetch_and_store(run, hits, iteration, seen_hashes)
            new_items = [
                {
                    "id": str(item.content_id),
                    "title": item.title,
                    "url": item.url or "",
                    "snippet": item.body[:900],
                }
                for item in items
            ]

            if not new_items:
                history.append(
                    {
                        "iteration": iteration,
                        "queries": queries,
                        "hit_count": len(hits),
                        "note": "Nothing new to compare.",
                        "kept_count": 0,
                    }
                )
                run.iterations_done = iteration
                await self._save_round(
                    run,
                    iteration,
                    queries,
                    hits,
                    content_ids=[],
                    kept_ids=[],
                    note="Nothing new to compare.",
                    history=history,
                )
                continue

            verdict = await self._match_subbatches(run, dots, new_items)
            kept_ids, dot_entries = self._apply_match(items, verdict, iteration)
            dots.extend(dot_entries)
            extra = run.metadata.get("connections") or []
            extra.extend(verdict.get("connections") or [])
            run.metadata["connections"] = extra

            history.append(
                {
                    "iteration": iteration,
                    "queries": queries,
                    "hit_count": len(hits),
                    "item_count": len(items),
                    "kept_count": len(kept_ids),
                    "note": verdict.get("note") or "",
                }
            )
            run.iterations_done = iteration
            run.dots_kept = len(dots)
            await self._save_round(
                run,
                iteration,
                queries,
                hits,
                content_ids=[item.content_id for item in items],
                kept_ids=kept_ids,
                note=verdict.get("note") or "",
                history=history,
            )

            cooldown = self._settings.dots_batch_cooldown_seconds
            if cooldown > 0:
                await asyncio.sleep(cooldown)

        run.reasoning_log = history
        run.metadata["elapsed_seconds"] = round(time.monotonic() - started_at, 1)
        synthesis = await self._researcher.synthesise(run.topic, dots, history)
        return await self._finish(run, dots, synthesis)

    async def _gather_hits(
        self, run: DotRun, queries: list[str], seen_urls: set[str]
    ) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for query in queries:
            for hit in await self._search.search_all(query, list(run.providers)):
                if not hit.url or hit.url in seen_urls:
                    continue
                seen_urls.add(hit.url)
                hits.append(hit)
        return hits

    async def _fetch_and_store(
        self,
        run: DotRun,
        hits: list[SearchHit],
        iteration: int,
        seen_hashes: set[str],
    ) -> list[ContentItem]:
        """Fetch pages concurrently and persist fresh ones as evidence."""
        limit = run.max_items_per_round
        cap = min(len(hits), limit)
        if cap <= 0:
            return []
        semaphore = asyncio.Semaphore(max(1, self._settings.dots_scrape_concurrency))

        async def _one(hit: SearchHit) -> ContentItem | None:
            async with semaphore:
                title, body = await self._search.fetch_page(hit.url)
                if not title.strip() and not body.strip():
                    return None
                fingerprint = content_hash(url=hit.url, title=title, body=body)
                if fingerprint in seen_hashes:
                    return None
                seen_hashes.add(fingerprint)
                if await self._repository.content_exists(fingerprint):
                    return None
                item = ContentItem(
                    source="dots",
                    source_type=SourceType.DOTS,
                    url=hit.url,
                    title=title[:512],
                    body=body[:200_000],
                    content_hash=fingerprint,
                    metadata={
                        "dot_run_id": str(run.dot_run_id),
                        "iteration": iteration,
                        "provider": hit.provider,
                    },
                    tags=["dots", run.topic.lower()[:32]],
                )
                await self._repository.add_content(item)
                return item

        gathered = await asyncio.gather(*(_one(hit) for hit in hits[:cap]))
        items = [found for found in gathered if found]
        run.evidence_count += len(items)
        return items

    async def _match_subbatches(
        self,
        run: DotRun,
        dots: list[dict[str, Any]],
        new_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Split the fresh batch into small chunks, match each against Oracle
        (concurrently, so parallel GPU slots on the laptop can be used), then
        merge the chunk verdicts back into the filtered batch."""
        chunks = _chunk_items(new_items, max(1, int(self._settings.dots_subbatches)))
        results = await asyncio.gather(
            *(self._researcher.match(run.topic, dots, chunk) for chunk in chunks if chunk),
            return_exceptions=True,
        )
        verdicts: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result, BaseException):
                log.warning(
                    "dots_subbatch_failed",
                    extra={"run": str(run.dot_run_id), "error": str(result)},
                )
                continue
            verdicts.append(result)
        return self._merge_verdicts(verdicts)

    @staticmethod
    def _merge_verdicts(verdicts: list[dict[str, Any]]) -> dict[str, Any]:
        kept: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        connections: list[dict[str, Any]] = []
        notes: list[str] = []
        for verdict in verdicts:
            for item in verdict.get("kept") or []:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or "")
                if not item_id or item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                kept.append(item)
            connections.extend(verdict.get("connections") or [])
            note = str(verdict.get("note") or "").strip()
            if note:
                notes.append(note)
        return {
            "kept": kept,
            "connections": connections,
            "note": " | ".join(notes),
        }

    def _apply_match(
        self,
        items: list[ContentItem],
        verdict: dict[str, Any],
        iteration: int,
    ) -> tuple[list[UUID], list[dict[str, Any]]]:
        by_id = {str(item.content_id): item for item in items}
        kept_ids: list[UUID] = []
        entries: list[dict[str, Any]] = []
        for kept in verdict.get("kept") or []:
            content_id = kept.get("id")
            item = by_id.get(content_id)
            if item is None:
                continue
            kept_ids.append(item.content_id)
            entries.append(
                {
                    "id": str(item.content_id),
                    "url": item.url,
                    "label": kept.get("label") or item.title,
                    "relevance": float(kept.get("relevance") or 0.0),
                    "reason": kept.get("reason") or "",
                    "iteration": iteration,
                }
            )
        return kept_ids, entries

    async def _save_round(
        self,
        run: DotRun,
        iteration: int,
        queries: list[str],
        hits: list[SearchHit],
        *,
        content_ids: list[UUID],
        kept_ids: list[UUID],
        note: str,
        history: list[dict[str, Any]],
    ) -> None:
        run.reasoning_log = history
        batch = DotBatch(
            dot_run_id=run.dot_run_id,
            iteration=iteration,
            queries=queries,
            hits_found=len(hits),
            content_ids=content_ids,
            kept_ids=kept_ids,
            note=note,
        )
        await self._repository.save_dot_batch(batch)
        await self._repository.save_dot_run(run)

    async def _finish(
        self,
        run: DotRun,
        dots: list[dict[str, Any]],
        synthesis: dict[str, Any],
    ) -> DotRun:
        report = Report(
            title=f"Dot investigation — {run.topic[:220]}",
            summary=synthesis.get("summary") or "",
            sections=[
                {
                    "key": "reasoning_log",
                    "title": "Reasoning log",
                    "body": synthesis.get("reasoning_log") or "",
                },
                {
                    "key": "key_findings",
                    "title": "Key findings",
                    "items": synthesis.get("key_findings") or [],
                },
                {
                    "key": "dots",
                    "title": "Relevant dots collected",
                    "items": [
                        {
                            "label": item["label"],
                            "url": item.get("url", ""),
                            "reason": item.get("reason", ""),
                            "relevance": item.get("relevance", 0.0),
                            "iteration": item.get("iteration", 0),
                        }
                        for item in dots
                    ],
                },
            ],
            evidence_ids=[UUID(item["id"]) for item in dots if item.get("id")],
            status=ReportStatus.FINAL,
            metadata={
                "source": "dots",
                "dot_run_id": str(run.dot_run_id),
                "topic": run.topic,
                "iterations_done": run.iterations_done,
            },
        )
        await self._repository.save_report(report)
        run.report_id = report.report_id
        run.summary = synthesis.get("summary") or ""
        run.reasoning_log = list(run.reasoning_log) + [
            {
                "stage": "synthesis",
                "reasoning_log": synthesis.get("reasoning_log") or "",
            }
        ]
        run.status = DotRunStatus.COMPLETED
        await self._repository.save_dot_run(run)
        await self._publish(run, report)
        await self._finish_watch(run, dots)
        log.info(
            "dots_run_completed",
            extra={
                "run": str(run.dot_run_id),
                "topic": run.topic,
                "dots": run.dots_kept,
                "iterations": run.iterations_done,
            },
        )
        return run

    async def _publish(self, run: DotRun, report: Report) -> None:
        if self._publisher is None:
            return
        await self._publisher.publish(
            HermesEvent(
                type=EVENT_REPORT_PUBLISHED,
                severity="info",
                title=f"Dot investigation complete: {run.topic[:220]}",
                message=(run.summary or f"Investigating: {run.topic}")[:16384],
                metadata={
                    "report_id": str(report.report_id),
                    "dot_run_id": str(run.dot_run_id),
                    "topic": run.topic,
                    "dots_kept": run.dots_kept,
                    "evidence": run.evidence_count,
                    "iterations": run.iterations_done,
                },
                tags=["argus", "dots", "report"],
            )
        )

    async def _finish_watch(self, run: DotRun, dots: list[dict[str, Any]]) -> None:
        """Store a watch-aware run's delta and notify Hermes when new dots appear."""
        watch_ref = run.metadata.get("watch")
        if not watch_ref:
            return
        watch_id = watch_ref.get("watch_id")
        if not watch_id:
            return
        try:
            watch = await self._repository.get_dot_watch(UUID(watch_id))
        except Exception:  # noqa: BLE001 - a stale ref must not break completion
            log.error("dots_watch_lookup_failed", extra={"watch": watch_id})
            return
        if watch is None:
            return
        now = utcnow()
        dot_ids = [str(item["id"]) for item in dots if item.get("id")]
        previous = set(watch.last_dot_ids or [])
        new_dots = [item for item in dots if str(item.get("id")) not in previous]
        await self._repository.complete_dot_watch(
            watch.dot_watch_id,
            run_id=run.dot_run_id,
            at=now,
            dot_ids=dot_ids,
        )
        if self._publisher is None:
            return
        await self._publisher.publish(
            HermesEvent(
                type=EVENT_DOTS_WATCH_UPDATE,
                severity="info",
                title=f"Watch update: {watch.topic[:220]}",
                message=_watch_summary(watch.topic, len(new_dots), len(dots)),
                metadata={
                    "watch_id": str(watch.dot_watch_id),
                    "topic": watch.topic,
                    "run_id": str(run.dot_run_id),
                    "report_id": str(run.report_id) if run.report_id else None,
                    "dots_total": len(dots),
                    "dots_new": len(new_dots),
                    "new_dots": [
                        {
                            "label": item.get("label", ""),
                            "url": item.get("url", ""),
                        }
                        for item in new_dots[:5]
                    ],
                },
                tags=["argus", "dots", "watch"],
            )
        )
