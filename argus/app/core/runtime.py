"""Argus runtime — wires collectors, intelligence, storage and publishing.

The runtime is the only place that knows how the pieces fit together:

    Collector -> ContentItem -> storage (evidence)
                              -> entity extraction
                              -> entity resolution (knowledge graph)
                              -> correlation (relations)
                              -> change detection
                              -> hypothesis engine (Oracle)
                              -> report generator
                             confirmed hypotheses / reports / alerts -> Hermes

It never notifies anyone directly and never acts; it only collects,
understands and publishes standardized events to Hermes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx

from app.config.loader import load_sources_config
from app.config.settings import Settings
from app.core.clients import Clients
from app.core.logging import get_logger
from app.core.oracle import OracleClient
from app.core.publisher import EventPublisher, HermesPublisher, NullPublisher
from app.core.scheduler import CollectorScheduler
from app.database.repository import Repository
from app.database.session import Database
from app.dots import (
    DotEnqueueError,
    DotResearcher,
    DotsEngine,
    ResearchCoordinator,
    WebSearchClient,
)
from app.intelligence.changes import ChangeDetector
from app.intelligence.correlate import Correlator
from app.intelligence.extract import EntityExtractor, RawEntity
from app.intelligence.hypotheses import HypothesisEngine, persist_and_promote
from app.intelligence.resolve import EntityResolver
from app.models.base import utcnow
from app.models.change import Change, ChangeType
from app.models.content import ContentItem, Severity
from app.models.dots import DotRunStatus
from app.models.entity import Entity
from app.models.event import (
    EVENT_CHANGE_DETECTED,
    EVENT_HYPOTHESIS_CONFIRMED,
    EVENT_REPORT_PUBLISHED,
    HERMES_SEVERITY,
    HermesEvent,
)
from app.models.hypothesis import Hypothesis, HypothesisStatus
from app.reports.generator import ReportGenerator
from app.sources.registry import default_registry

log = get_logger(__name__)


class ArgusRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database: Database | None = None
        self.repository: Repository | None = None
        self.clients: Clients | None = None
        self.publisher: EventPublisher | None = None
        self.oracle: OracleClient | None = None
        self.extractor: EntityExtractor | None = None
        self.resolver: EntityResolver | None = None
        self.correlator: Correlator | None = None
        self.changes: ChangeDetector | None = None
        self.hypotheses: HypothesisEngine | None = None
        self.reports: ReportGenerator | None = None
        self.scheduler: CollectorScheduler | None = None
        self.collectors: list = []
        self.dots: DotsEngine | None = None
        self.research: ResearchCoordinator | None = None
        self._dots_http: httpx.AsyncClient | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self.started_at = utcnow()
        self.last_intelligence_tick: Any = None

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        settings = self.settings
        database = Database(settings.database_url)
        await database.init()
        self.database = database
        self.repository = Repository(database.session_factory)
        stale = await self.repository.fail_stale_dot_runs()
        if stale:
            log.warning("dots_stale_runs_failed", extra={"count": stale})
        stale_sessions = await self.repository.fail_stale_research_sessions()
        if stale_sessions:
            log.warning("research_stale_sessions_failed", extra={"count": stale_sessions})

        sources_config = load_sources_config(settings.sources_file)

        self.clients = Clients.defaults(timeout=settings.collector_timeout)
        self.publisher = (
            HermesPublisher(settings, client=self.clients.http)
            if settings.hermes_enabled
            else NullPublisher()
        )

        if settings.oracle_enabled:
            self.oracle = OracleClient(settings)

        repository = self.repository
        self.extractor = EntityExtractor(settings, oracle=self.oracle)
        self.resolver = EntityResolver(repository)
        self.correlator = Correlator(repository)
        self.changes = ChangeDetector(repository)
        self.hypotheses = HypothesisEngine(settings, oracle=self.oracle)
        self.reports = ReportGenerator(settings, repository)

        registry = default_registry()
        self.collectors = registry.build_all(sources_config, settings, self.clients)
        self.scheduler = CollectorScheduler(
            self.collectors,
            default_timeout=settings.collector_timeout,
            jitter=settings.collector_jitter,
            backoff_base=settings.collector_backoff_base,
            backoff_max=settings.collector_backoff_max,
            failure_threshold=settings.collector_failure_threshold,
            publisher=self.publisher,
        )
        if self.collectors:
            self.scheduler.start(self.ingest)

        self._tasks.append(
            asyncio.create_task(
                self._tick(self._report_tick, settings.intelligence_scan_interval, "reports")
            )
        )

        if settings.dots_enabled and self.oracle is not None:
            self._dots_http = httpx.AsyncClient(
                timeout=settings.dots_scrape_timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Argus/1.0"
                    )
                },
            )
            self.dots = DotsEngine(
                settings=settings,
                repository=repository,
                oracle=self.oracle,
                search=WebSearchClient(self._dots_http, timeout=settings.dots_scrape_timeout),
                researcher=DotResearcher(self.oracle),
                publisher=self.publisher,
            )
            self._tasks.append(
                asyncio.create_task(
                    self._tick(self._dots_tick, settings.dots_worker_interval, "dots")
                )
            )
            if settings.dots_watch_enabled:
                self._tasks.append(
                    asyncio.create_task(
                        self._tick(
                            self._dots_watch_tick,
                            settings.dots_watch_interval,
                            "dots-watch",
                        )
                    )
                )
            if settings.research_enabled:
                self.research = ResearchCoordinator(
                    settings=settings,
                    repository=repository,
                    oracle=self.oracle,
                    researcher=DotResearcher(self.oracle),
                    dots=self.dots,
                    publisher=self.publisher,
                )
                self._tasks.append(
                    asyncio.create_task(
                        self._tick(
                            self.research.tick,
                            settings.research_worker_interval,
                            "research",
                        )
                    )
                )

        log.info(
            "argus_started",
            extra={
                "version": settings.version,
                "environment": settings.environment,
                "collectors": [collector.name for collector in self.collectors],
                "oracle": settings.oracle_enabled,
                "hermes": settings.hermes_event_url if settings.hermes_enabled else "disabled",
            },
        )

    async def stop(self) -> None:
        log.info("argus_stopping")
        if self.scheduler is not None:
            await self.scheduler.stop()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        if self.oracle is not None:
            await self.oracle.close()
        if self._dots_http is not None:
            await self._dots_http.aclose()
        if self.publisher is not None:
            await self.publisher.aclose()
        if self.clients is not None:
            await self.clients.aclose()
        if self.database is not None:
            await self.database.dispose()
        log.info("argus_stopped")

    # ------------------------------------------------------------------ ticking
    async def _tick(self, handler, interval: float, name: str) -> None:
        try:
            while True:
                await asyncio.sleep(interval)
                await handler()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never kill the runtime
            log.error("tick_failed", extra={"tick": name, "error": str(exc)})

    async def _report_tick(self) -> None:
        """Sweep for confirmed hypotheses not yet covered by a report."""
        self.last_intelligence_tick = utcnow()
        assert self.repository is not None
        assert self.reports is not None
        confirmed = await self.repository.confirmed_hypotheses(limit=50)
        if not confirmed:
            return
        _, total = await self.repository.list_reports(limit=1)
        latest = None
        if total:
            reports, _ = await self.repository.list_reports(limit=1)
            latest = reports[0]
        newest = [h for h in confirmed if latest is None or h.updated_at > latest.created_at]
        if not newest:
            return
        report = await self.reports.generate(newest)
        if report is not None:
            await self._publish_report(report)

    async def _dots_tick(self) -> None:
        """Run queued dot investigations one at a time (serial worker)."""
        if self.dots is None or self.repository is None:
            return
        queued, total = await self.repository.list_dot_runs(
            status=DotRunStatus.QUEUED.value, limit=1
        )
        if not queued:
            return
        await self.dots.execute(queued[0].dot_run_id)

    async def _dots_watch_tick(self) -> None:
        """Enqueue due watch topics one at a time and reschedule them."""
        if self.dots is None or self.repository is None:
            return
        due = await self.repository.list_due_dot_watches(utcnow(), limit=1)
        if not due:
            return
        watch = due[0]
        try:
            await self.dots.enqueue(
                watch.topic,
                iterations=watch.iterations,
                providers=watch.providers,
                queries_per_round=watch.queries_per_round,
                max_items_per_round=watch.max_items_per_round,
                watch_id=str(watch.dot_watch_id),
            )
        except DotEnqueueError:
            log.warning(
                "dots_watch_enqueue_failed",
                extra={"watch": str(watch.dot_watch_id), "topic": watch.topic},
            )
            return
        await self.repository.mark_dot_watch_queued(
            watch.dot_watch_id, utcnow() + timedelta(hours=watch.interval_hours)
        )
        log.info(
            "dots_watch_queued",
            extra={"watch": str(watch.dot_watch_id), "topic": watch.topic},
        )

    # ------------------------------------------------------------------ ingest
    async def ingest(self, items: Sequence[ContentItem]) -> None:
        """Store new evidence and run the intelligence pipeline over it."""
        assert self.repository is not None
        new_items: list[ContentItem] = []
        for item in items:
            if await self.repository.content_exists(item.content_hash):
                continue
            await self.repository.add_content(item)
            new_items.append(item)
        if not new_items:
            return
        log.info("evidence_ingested", extra={"items": len(new_items)})
        await self._intelligence_pass(new_items)

    # ---------------------------------------------------------- intelligence
    async def _intelligence_pass(self, items: list[ContentItem]) -> None:
        assert self.repository is not None
        assert self.extractor is not None
        assert self.resolver is not None
        assert self.correlator is not None
        assert self.changes is not None
        assert self.hypotheses is not None
        assert self.reports is not None

        # 1. Extract raw entities per item (Oracle calls run concurrently so a
        #    large feed batch does not stall the pipeline for minutes).
        raw_by_item: dict[UUID, list[RawEntity]] = {}
        extractor = self.extractor
        semaphore = asyncio.Semaphore(max(1, self.settings.oracle_extraction_concurrency))

        async def _extract(item: ContentItem) -> tuple[UUID, list[RawEntity]]:
            async with semaphore:
                return item.content_id, await extractor.extract(item)

        for content_id, extracted in await asyncio.gather(*(_extract(item) for item in items)):
            raw_by_item[content_id] = extracted

        # 2. Resolve into knowledge-graph entities.
        created_entities: list[Entity] = []
        entities_by_item: dict[UUID, list[Entity]] = {}
        for item in items:
            created, _ = await self.resolver.resolve(
                raw_by_item[item.content_id],
                evidence_id=item.content_id,
                source=item.source,
            )
            created_entities.extend(created)
            entities_by_item[item.content_id] = created

        # 3. Correlate co-occurrence into relations.
        relations = await self.correlator.correlate(
            [(item.content_id, entities_by_item[item.content_id]) for item in items]
        )

        # 4. Detect changes and persist them.
        changes = await self.changes.detect(items, created_entities, relations)
        for change in changes:
            await self.repository.add_change(change)

        # 5. Generate and persist hypotheses.
        hypotheses = await self.hypotheses.generate(
            items=items,
            entities_by_item=entities_by_item,
            changes=changes,
        )
        saved = await persist_and_promote(self.repository, hypotheses)

        # 6. Publish notable findings.
        for change in changes:
            await self._publish_change(change)
        for hypothesis in saved:
            if hypothesis.status == HypothesisStatus.CONFIRMED:
                await self._publish_hypothesis(hypothesis)
        report = await self.reports.generate(saved)
        if report is not None:
            await self._publish_report(report)

    # ---------------------------------------------------------------- publish
    async def _publish_change(self, change: Change) -> None:
        if change.severity not in (Severity.HIGH, Severity.CRITICAL):
            return
        assert self.publisher is not None
        await self.publisher.publish(
            HermesEvent(
                type=EVENT_CHANGE_DETECTED,
                severity=HERMES_SEVERITY[change.severity.value],
                title=f"{change.change_type.value.replace('_', ' ').title()}: {change.object_key}",
                message=_change_message(change),
                metadata={
                    "change_id": str(change.change_id),
                    "change_type": change.change_type.value,
                    "object_key": change.object_key,
                    "severity": change.severity.value,
                    "confidence": change.confidence,
                },
                tags=["argus", "change", change.change_type.value],
            )
        )

    async def _publish_hypothesis(self, hypothesis: Hypothesis) -> None:
        assert self.publisher is not None
        await self.publisher.publish(
            HermesEvent(
                type=EVENT_HYPOTHESIS_CONFIRMED,
                severity=HERMES_SEVERITY[_severity_for_confidence(hypothesis.confidence)],
                title=f"Hypothesis confirmed: {hypothesis.statement[:180]}",
                message=hypothesis.rationale,
                metadata={
                    "hypothesis_id": str(hypothesis.hypothesis_id),
                    "confidence": hypothesis.confidence,
                    "evidence_count": len(hypothesis.evidence_ids),
                    "oracle_generated": hypothesis.oracle_generated,
                },
                tags=["argus", "hypothesis"],
            )
        )

    async def _publish_report(self, report) -> None:
        assert self.publisher is not None
        await self.publisher.publish(
            HermesEvent(
                type=EVENT_REPORT_PUBLISHED,
                severity="info",
                title=f"Intelligence report: {report.title}",
                message=report.summary,
                metadata={
                    "report_id": str(report.report_id),
                    "hypotheses": len(report.hypothesis_ids),
                    "evidence": len(report.evidence_ids),
                    "entities": len(report.entity_ids),
                },
                tags=["argus", "report"],
            )
        )


def _change_message(change: Change) -> str:
    if change.change_type == ChangeType.CONTENT_CHANGED:
        return f"Tracked page {change.object_key} changed " f"(confidence {change.confidence:.2f})."
    return (
        f"{change.change_type.value.replace('_', ' ').title()} on "
        f"{change.object_key} (confidence {change.confidence:.2f})."
    )


def _severity_for_confidence(confidence: float) -> str:
    if confidence >= 0.9:
        return "error"
    if confidence >= 0.8:
        return "warning"
    return "info"
