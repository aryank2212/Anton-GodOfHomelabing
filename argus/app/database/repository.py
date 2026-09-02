"""Repository — all persistence for Argus.

Hides SQLAlchemy from the rest of the application: engines and API code only
see typed domain models. Timestamps round-trip as UTC (see
:mod:`app.database.session`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, overload
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.database.models import (
    ChangeRecord,
    DotBatchRecord,
    DotRunRecord,
    DotWatchRecord,
    EntityRecord,
    EntityRelationRecord,
    EvidenceRecord,
    HypothesisRecord,
    ReportRecord,
    ResearchSessionRecord,
)
from app.models.change import Change, ChangeType
from app.models.content import ContentItem, Severity, SourceType
from app.models.dots import DotBatch, DotRun, DotRunStatus, DotWatch
from app.models.entity import Entity, EntityKind, EntityRelation
from app.models.hypothesis import Hypothesis, HypothesisStatus
from app.models.report import Report, ReportStatus
from app.models.research import (
    ResearchSession,
    ResearchSessionMode,
    ResearchSessionStatus,
)

log = get_logger(__name__)


@overload
def _store_utc(value: None) -> None: ...


@overload
def _store_utc(value: datetime) -> datetime: ...


def _store_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC).replace(tzinfo=None)


def _load_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC)


def _load_required(value: datetime | None) -> datetime:
    if value is None:
        raise ValueError("unexpected null value for required timestamp")
    return value.replace(tzinfo=UTC)


def _column_kwargs(data: dict[str, Any]) -> dict[str, Any]:
    """Map domain record keys onto SQLAlchemy attribute names.

    The ORM keeps ``metadata_`` as the Python attribute for the ``metadata``
    column (the bare name collides with ``Base.metadata``, the declarative
    schema object). Without this translation every ``metadata=`` insert and
    ``setattr(record, "metadata", ...)`` update would be silently dropped.
    """
    mapped = dict(data)
    if "metadata" in mapped:
        mapped["metadata_"] = mapped.pop("metadata")
    return mapped


# ------------------------------------------------------------------ evidence


def _content_from_row(row: EvidenceRecord) -> ContentItem:
    return ContentItem(
        content_id=UUID(row.content_id),
        source=row.source,
        source_type=SourceType(row.source_type),
        url=row.url,
        title=row.title,
        body=row.body,
        content_hash=row.content_hash,
        language=row.language,
        fetched_at=_load_required(row.fetched_at),
        metadata=dict(row.metadata_ or {}),
        tags=list(row.tags or []),
    )


# ------------------------------------------------------------------ entities


def _entity_from_row(row: EntityRecord) -> Entity:
    return Entity(
        entity_id=UUID(row.entity_id),
        kind=EntityKind(row.kind),
        name=row.name,
        aliases=list(row.aliases or []),
        attributes=dict(row.attributes or {}),
        confidence=row.confidence,
        first_seen=_load_required(row.first_seen),
        last_seen=_load_required(row.last_seen),
        times_seen=row.times_seen,
        source=row.source,
    )


def _relation_from_row(row: EntityRelationRecord) -> EntityRelation:
    return EntityRelation(
        relation_id=UUID(row.relation_id),
        subject_id=UUID(row.subject_id),
        relation=row.relation,
        object_id=UUID(row.object_id),
        evidence_ids=[UUID(value) for value in (row.evidence_ids or [])],
        confidence=row.confidence,
        first_seen=_load_required(row.first_seen),
        last_seen=_load_required(row.last_seen),
        times_seen=row.times_seen,
    )


# ------------------------------------------------------------------- changes


def _change_from_row(row: ChangeRecord) -> Change:
    return Change(
        change_id=UUID(row.change_id),
        change_type=ChangeType(row.change_type),
        object_key=row.object_key,
        attribute=row.attribute,
        before=row.before,
        after=row.after,
        evidence_id=UUID(row.evidence_id) if row.evidence_id else None,
        severity=Severity(row.severity),
        confidence=row.confidence,
        detected_at=_load_required(row.detected_at),
        metadata=dict(row.metadata_ or {}),
    )


# ---------------------------------------------------------------- hypotheses


def _hypothesis_from_row(row: HypothesisRecord) -> Hypothesis:
    return Hypothesis(
        hypothesis_id=UUID(row.hypothesis_id),
        statement=row.statement,
        rationale=row.rationale,
        evidence_ids=[UUID(value) for value in (row.evidence_ids or [])],
        entity_ids=[UUID(value) for value in (row.entity_ids or [])],
        confidence=row.confidence,
        status=HypothesisStatus(row.status),
        oracle_generated=row.oracle_generated,
        created_at=_load_required(row.created_at),
        updated_at=_load_required(row.updated_at),
        metadata=dict(row.metadata_ or {}),
    )


# ------------------------------------------------------------------- reports


def _report_from_row(row: ReportRecord) -> Report:
    return Report(
        report_id=UUID(row.report_id),
        title=row.title,
        summary=row.summary,
        sections=list(row.sections or []),
        hypothesis_ids=[UUID(value) for value in (row.hypothesis_ids or [])],
        evidence_ids=[UUID(value) for value in (row.evidence_ids or [])],
        entity_ids=[UUID(value) for value in (row.entity_ids or [])],
        status=ReportStatus(row.status),
        created_at=_load_required(row.created_at),
        metadata=dict(row.metadata_ or {}),
    )


# ----------------------------------------------------------------------- dots


def _dot_run_from_row(row: DotRunRecord) -> DotRun:
    return DotRun(
        dot_run_id=UUID(row.dot_run_id),
        topic=row.topic,
        status=DotRunStatus(row.status),
        iterations_target=row.iterations_target,
        iterations_done=row.iterations_done,
        providers=list(row.providers or []),
        queries_per_round=row.queries_per_round,
        max_items_per_round=row.max_items_per_round,
        dots_kept=row.dots_kept,
        evidence_count=row.evidence_count,
        summary=row.summary,
        reasoning_log=list(row.reasoning_log or []),
        report_id=UUID(row.report_id) if row.report_id else None,
        session_id=UUID(row.session_id) if row.session_id else None,
        error=row.error,
        created_at=_load_required(row.created_at),
        updated_at=_load_required(row.updated_at),
        metadata=dict(row.metadata_ or {}),
    )


def _research_session_from_row(row: ResearchSessionRecord) -> ResearchSession:
    return ResearchSession(
        research_session_id=UUID(row.research_session_id),
        question=row.question,
        context=row.context,
        mode=ResearchSessionMode(row.mode),
        status=ResearchSessionStatus(row.status),
        max_angles=row.max_angles,
        angles_planned=list(row.angles_planned or []),
        runs_completed=row.runs_completed,
        summary=row.summary,
        report_id=UUID(row.report_id) if row.report_id else None,
        error=row.error,
        created_at=_load_required(row.created_at),
        updated_at=_load_required(row.updated_at),
        started_at=_load_utc(row.started_at),
        finished_at=_load_utc(row.finished_at),
        metadata=dict(row.metadata_ or {}),
    )


def _dot_watch_from_row(row: DotWatchRecord) -> DotWatch:
    return DotWatch(
        dot_watch_id=UUID(row.dot_watch_id),
        topic=row.topic,
        iterations=row.iterations,
        providers=list(row.providers or []),
        queries_per_round=row.queries_per_round,
        max_items_per_round=row.max_items_per_round,
        interval_hours=row.interval_hours,
        enabled=bool(row.enabled),
        next_run_at=_load_required(row.next_run_at),
        last_run_id=UUID(row.last_run_id) if row.last_run_id else None,
        last_run_at=_load_utc(row.last_run_at),
        last_dot_ids=list(row.last_dot_ids or []),
        created_at=_load_required(row.created_at),
        updated_at=_load_required(row.updated_at),
    )


def _dot_batch_from_row(row: DotBatchRecord) -> DotBatch:
    return DotBatch(
        batch_id=UUID(row.batch_id),
        dot_run_id=UUID(row.dot_run_id),
        iteration=row.iteration,
        queries=list(row.queries or []),
        hits_found=row.hits_found,
        content_ids=[UUID(value) for value in (row.content_ids or [])],
        kept_ids=[UUID(value) for value in (row.kept_ids or [])],
        note=row.note,
        created_at=_load_required(row.created_at),
    )


class Repository:
    """Persistence surface. Sessions are opened per operation."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session = session_factory

    # ------------------------------------------------------------- evidence
    async def add_content(self, item: ContentItem) -> None:
        async with self._session() as session:
            session.add(EvidenceRecord(**_column_kwargs(item.to_record_dict())))
            await session.commit()

    async def get_content(self, content_id: UUID) -> ContentItem | None:
        async with self._session() as session:
            row = await session.get(EvidenceRecord, str(content_id))
            return _content_from_row(row) if row else None

    async def content_exists(self, content_hash: str) -> bool:
        async with self._session() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(EvidenceRecord)
                .where(EvidenceRecord.content_hash == content_hash)
            )
            return bool(count)

    async def latest_for_url(
        self, url: str, *, exclude_id: UUID | None = None
    ) -> ContentItem | None:
        """Newest stored item for a URL (used by change detection).

        ``exclude_id`` lets callers skip the item currently being processed:
        ingest persists evidence *before* the intelligence pass runs, so without
        this the detector would compare a scrape against itself and never see
        that its content changed.
        """
        async with self._session() as session:
            stmt = select(EvidenceRecord).where(EvidenceRecord.url == url)
            if exclude_id is not None:
                stmt = stmt.where(EvidenceRecord.content_id != str(exclude_id))
            row = (
                await session.execute(stmt.order_by(EvidenceRecord.fetched_at.desc()).limit(1))
            ).scalar_one_or_none()
            return _content_from_row(row) if row else None

    async def list_content(
        self,
        *,
        source: str | None = None,
        source_type: str | None = None,
        q: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ContentItem], int]:
        async with self._session() as session:
            stmt = select(EvidenceRecord)
            if source:
                stmt = stmt.where(EvidenceRecord.source == source)
            if source_type:
                stmt = stmt.where(EvidenceRecord.source_type == source_type)
            if q:
                pattern = f"%{q}%"
                stmt = stmt.where(
                    or_(
                        EvidenceRecord.title.ilike(pattern),
                        EvidenceRecord.body.ilike(pattern),
                        EvidenceRecord.url.ilike(pattern),
                    )
                )
            if since:
                stmt = stmt.where(EvidenceRecord.fetched_at >= _store_utc(since))
            if until:
                stmt = stmt.where(EvidenceRecord.fetched_at <= _store_utc(until))
            total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (
                (
                    await session.execute(
                        stmt.order_by(
                            EvidenceRecord.fetched_at.desc(), EvidenceRecord.content_id.desc()
                        )
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            return [_content_from_row(row) for row in rows], total or 0

    async def recent_content(self, *, days: int = 1, limit: int = 10_000) -> list[ContentItem]:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        async with self._session() as session:
            rows = (
                (
                    await session.execute(
                        select(EvidenceRecord)
                        .where(EvidenceRecord.fetched_at >= _store_utc(cutoff))
                        .order_by(EvidenceRecord.fetched_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return [_content_from_row(row) for row in rows]

    async def count_content(self) -> int:
        async with self._session() as session:
            return await session.scalar(select(func.count()).select_from(EvidenceRecord)) or 0

    # ------------------------------------------------------------- entities
    async def save_entity(self, entity: Entity) -> None:
        data = _column_kwargs(entity.to_record_dict())
        async with self._session() as session:
            record = await session.get(EntityRecord, str(entity.entity_id))
            if record is None:
                session.add(EntityRecord(**data))
            else:
                for key, value in data.items():
                    setattr(record, key, value)
            await session.commit()

    async def get_entity(self, entity_id: UUID) -> Entity | None:
        async with self._session() as session:
            row = await session.get(EntityRecord, str(entity_id))
            return _entity_from_row(row) if row else None

    async def get_entity_by_key(self, name_key: str) -> Entity | None:
        async with self._session() as session:
            row = (
                await session.execute(select(EntityRecord).where(EntityRecord.name_key == name_key))
            ).scalar_one_or_none()
            return _entity_from_row(row) if row else None

    async def list_entities(
        self,
        *,
        kind: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Entity], int]:
        async with self._session() as session:
            stmt = select(EntityRecord)
            if kind:
                stmt = stmt.where(EntityRecord.kind == kind)
            if q:
                pattern = f"%{q}%"
                stmt = stmt.where(
                    or_(
                        EntityRecord.name.ilike(pattern),
                        EntityRecord.name_key.ilike(pattern),
                    )
                )
            total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (
                (
                    await session.execute(
                        stmt.order_by(EntityRecord.last_seen.desc(), EntityRecord.name_key.desc())
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            return [_entity_from_row(row) for row in rows], total or 0

    async def recent_entities(self, *, days: int = 1, limit: int = 10_000) -> list[Entity]:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        async with self._session() as session:
            rows = (
                (
                    await session.execute(
                        select(EntityRecord)
                        .where(EntityRecord.last_seen >= _store_utc(cutoff))
                        .order_by(EntityRecord.last_seen.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return [_entity_from_row(row) for row in rows]

    async def count_entities(self) -> int:
        async with self._session() as session:
            return await session.scalar(select(func.count()).select_from(EntityRecord)) or 0

    # ------------------------------------------------------------- relations
    async def save_relation(self, relation: EntityRelation) -> None:
        data = _column_kwargs(relation.to_record_dict())
        async with self._session() as session:
            record = await session.get(EntityRelationRecord, str(relation.relation_id))
            if record is None:
                session.add(EntityRelationRecord(**data))
            else:
                for key, value in data.items():
                    setattr(record, key, value)
            await session.commit()

    async def get_relation(
        self, subject_id: UUID, relation: str, object_id: UUID
    ) -> EntityRelation | None:
        """Latest stored relation with this exact (subject, relation, object)."""
        async with self._session() as session:
            row = (
                await session.execute(
                    select(EntityRelationRecord)
                    .where(
                        EntityRelationRecord.subject_id == str(subject_id),
                        EntityRelationRecord.relation == relation,
                        EntityRelationRecord.object_id == str(object_id),
                    )
                    .order_by(EntityRelationRecord.last_seen.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            return _relation_from_row(row) if row else None

    async def list_relations(
        self,
        *,
        entity_id: UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[EntityRelation], int]:
        async with self._session() as session:
            stmt = select(EntityRelationRecord)
            if entity_id is not None:
                target = str(entity_id)
                stmt = stmt.where(
                    or_(
                        EntityRelationRecord.subject_id == target,
                        EntityRelationRecord.object_id == target,
                    )
                )
            total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (
                (
                    await session.execute(
                        stmt.order_by(
                            EntityRelationRecord.last_seen.desc(),
                            EntityRelationRecord.relation_id.desc(),
                        )
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            return [_relation_from_row(row) for row in rows], total or 0

    async def count_relations(self) -> int:
        async with self._session() as session:
            return await session.scalar(select(func.count()).select_from(EntityRelationRecord)) or 0

    # -------------------------------------------------------------- changes
    async def add_change(self, change: Change) -> None:
        async with self._session() as session:
            session.add(ChangeRecord(**_column_kwargs(change.to_record_dict())))
            await session.commit()

    async def list_changes(
        self,
        *,
        change_type: str | None = None,
        severity: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Change], int]:
        async with self._session() as session:
            stmt = select(ChangeRecord)
            if change_type:
                stmt = stmt.where(ChangeRecord.change_type == change_type)
            if severity:
                stmt = stmt.where(ChangeRecord.severity == severity)
            total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (
                (
                    await session.execute(
                        stmt.order_by(
                            ChangeRecord.detected_at.desc(), ChangeRecord.change_id.desc()
                        )
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            return [_change_from_row(row) for row in rows], total or 0

    async def count_changes(self) -> int:
        async with self._session() as session:
            return await session.scalar(select(func.count()).select_from(ChangeRecord)) or 0

    # ------------------------------------------------------------ hypotheses
    async def save_hypothesis(self, hypothesis: Hypothesis) -> None:
        data = _column_kwargs(hypothesis.to_record_dict())
        async with self._session() as session:
            record = await session.get(HypothesisRecord, str(hypothesis.hypothesis_id))
            if record is None:
                session.add(HypothesisRecord(**data))
            else:
                for key, value in data.items():
                    setattr(record, key, value)
            await session.commit()

    async def get_hypothesis(self, hypothesis_id: UUID) -> Hypothesis | None:
        async with self._session() as session:
            row = await session.get(HypothesisRecord, str(hypothesis_id))
            return _hypothesis_from_row(row) if row else None

    async def list_hypotheses(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Hypothesis], int]:
        async with self._session() as session:
            stmt = select(HypothesisRecord)
            if status:
                stmt = stmt.where(HypothesisRecord.status == status)
            total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (
                (
                    await session.execute(
                        stmt.order_by(
                            HypothesisRecord.updated_at.desc(),
                            HypothesisRecord.hypothesis_id.desc(),
                        )
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            return [_hypothesis_from_row(row) for row in rows], total or 0

    async def confirmed_hypotheses(self, *, limit: int = 50) -> list[Hypothesis]:
        async with self._session() as session:
            rows = (
                (
                    await session.execute(
                        select(HypothesisRecord)
                        .where(HypothesisRecord.status == HypothesisStatus.CONFIRMED.value)
                        .order_by(HypothesisRecord.updated_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return [_hypothesis_from_row(row) for row in rows]

    async def count_hypotheses(self, status: str | None = None) -> int:
        async with self._session() as session:
            stmt = select(func.count()).select_from(HypothesisRecord)
            if status:
                stmt = stmt.where(HypothesisRecord.status == status)
            return await session.scalar(stmt) or 0

    # --------------------------------------------------------------- reports
    async def save_report(self, report: Report) -> None:
        data = _column_kwargs(report.to_record_dict())
        async with self._session() as session:
            record = await session.get(ReportRecord, str(report.report_id))
            if record is None:
                session.add(ReportRecord(**data))
            else:
                for key, value in data.items():
                    setattr(record, key, value)
            await session.commit()

    async def get_report(self, report_id: UUID) -> Report | None:
        async with self._session() as session:
            row = await session.get(ReportRecord, str(report_id))
            return _report_from_row(row) if row else None

    async def list_reports(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Report], int]:
        async with self._session() as session:
            stmt = select(ReportRecord)
            if status:
                stmt = stmt.where(ReportRecord.status == status)
            total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (
                (
                    await session.execute(
                        stmt.order_by(ReportRecord.created_at.desc(), ReportRecord.report_id.desc())
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            return [_report_from_row(row) for row in rows], total or 0

    async def count_reports(self) -> int:
        async with self._session() as session:
            return await session.scalar(select(func.count()).select_from(ReportRecord)) or 0

    # ------------------------------------------------------------------- dots
    async def save_dot_run(self, run: DotRun) -> None:
        data = _column_kwargs(run.to_record_dict())
        async with self._session() as session:
            record = await session.get(DotRunRecord, str(run.dot_run_id))
            if record is None:
                session.add(DotRunRecord(**data))
            else:
                for key, value in data.items():
                    setattr(record, key, value)
            await session.commit()

    async def get_dot_run(self, dot_run_id: UUID) -> DotRun | None:
        async with self._session() as session:
            row = await session.get(DotRunRecord, str(dot_run_id))
            return _dot_run_from_row(row) if row else None

    async def list_dot_runs(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DotRun], int]:
        async with self._session() as session:
            stmt = select(DotRunRecord)
            if status:
                stmt = stmt.where(DotRunRecord.status == status)
            total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (
                (
                    await session.execute(
                        stmt.order_by(
                            DotRunRecord.created_at.desc(), DotRunRecord.dot_run_id.desc()
                        )
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            return [_dot_run_from_row(row) for row in rows], total or 0

    async def count_dot_runs(self, status: str | None = None) -> int:
        async with self._session() as session:
            stmt = select(func.count()).select_from(DotRunRecord)
            if status:
                stmt = stmt.where(DotRunRecord.status == status)
            return await session.scalar(stmt) or 0

    async def fail_stale_dot_runs(self) -> int:
        """Mark runs still RUNNING after a restart as failed.

        A dot run never survives a process exit: the engine keeps all state in
        memory, so any run that was mid-flight when Argus stopped is
        permanently stuck. Sweeping it to FAILED keeps the queue honest instead
        of leaving ghost "running" rows forever.
        """
        async with self._session() as session:
            rows = (
                (
                    await session.execute(
                        select(DotRunRecord)
                        .where(DotRunRecord.status == DotRunStatus.RUNNING.value)
                        .limit(200)
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                row.status = DotRunStatus.FAILED.value
                row.error = "interrupted by restart"
            await session.commit()
            return len(rows)

    async def save_dot_watch(self, watch: DotWatch) -> None:
        data = _column_kwargs(watch.to_record_dict())
        async with self._session() as session:
            record = await session.get(DotWatchRecord, str(watch.dot_watch_id))
            if record is None:
                session.add(DotWatchRecord(**data))
            else:
                for key, value in data.items():
                    setattr(record, key, value)
            await session.commit()

    async def get_dot_watch(self, dot_watch_id: UUID) -> DotWatch | None:
        async with self._session() as session:
            row = await session.get(DotWatchRecord, str(dot_watch_id))
            return _dot_watch_from_row(row) if row else None

    async def list_dot_watches(self) -> list[DotWatch]:
        async with self._session() as session:
            rows = (
                (
                    await session.execute(
                        select(DotWatchRecord).order_by(
                            DotWatchRecord.created_at.asc(), DotWatchRecord.dot_watch_id.asc()
                        )
                    )
                )
                .scalars()
                .all()
            )
            return [_dot_watch_from_row(row) for row in rows]

    async def list_due_dot_watches(
        self, now: datetime, limit: int = 1
    ) -> list[DotWatch]:
        """Watch items whose next run is due, oldest deadline first."""
        async with self._session() as session:
            rows = (
                (
                    await session.execute(
                        select(DotWatchRecord)
                        .where(
                            DotWatchRecord.enabled.is_(True),
                            DotWatchRecord.next_run_at <= _store_utc(now),
                        )
                        .order_by(DotWatchRecord.next_run_at.asc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return [_dot_watch_from_row(row) for row in rows]

    async def mark_dot_watch_queued(self, dot_watch_id: UUID, next_run_at: datetime) -> None:
        async with self._session() as session:
            record = await session.get(DotWatchRecord, str(dot_watch_id))
            if record is not None:
                record.next_run_at = _store_utc(next_run_at)
                record.updated_at = _store_utc(next_run_at)
                await session.commit()

    async def complete_dot_watch(
        self,
        dot_watch_id: UUID,
        *,
        run_id: UUID,
        at: datetime,
        dot_ids: list[str],
    ) -> None:
        async with self._session() as session:
            record = await session.get(DotWatchRecord, str(dot_watch_id))
            if record is None:
                return
            record.last_run_id = str(run_id)
            record.last_run_at = _store_utc(at)
            record.last_dot_ids = list(dot_ids)
            record.updated_at = _store_utc(at)
            await session.commit()

    async def delete_dot_watch(self, dot_watch_id: UUID) -> bool:
        async with self._session() as session:
            record = await session.get(DotWatchRecord, str(dot_watch_id))
            if record is None:
                return False
            await session.delete(record)
            await session.commit()
            return True

    async def save_dot_batch(self, batch: DotBatch) -> None:
        async with self._session() as session:
            session.add(DotBatchRecord(**batch.to_record_dict()))
            await session.commit()

    async def list_dot_batches(
        self,
        *,
        dot_run_id: UUID,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[DotBatch], int]:
        async with self._session() as session:
            stmt = (
                select(DotBatchRecord)
                .where(DotBatchRecord.dot_run_id == str(dot_run_id))
                .order_by(DotBatchRecord.iteration.asc())
            )
            total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (
                (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
            )
            return [_dot_batch_from_row(row) for row in rows], total or 0

    async def list_dot_runs_for_session(
        self, research_session_id: UUID, *, limit: int = 100
    ) -> list[DotRun]:
        """Every dot run belonging to a research session, newest first."""
        async with self._session() as session:
            rows = (
                (
                    await session.execute(
                        select(DotRunRecord)
                        .where(DotRunRecord.session_id == str(research_session_id))
                        .order_by(
                            DotRunRecord.created_at.asc(), DotRunRecord.dot_run_id.asc()
                        )
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return [_dot_run_from_row(row) for row in rows]

    # ------------------------------------------------------ research sessions
    async def save_research_session(self, session: ResearchSession) -> None:
        data = _column_kwargs(session.to_record_dict())
        async with self._session() as session_ctx:
            record = await session_ctx.get(
                ResearchSessionRecord, str(session.research_session_id)
            )
            if record is None:
                session_ctx.add(ResearchSessionRecord(**data))
            else:
                for key, value in data.items():
                    setattr(record, key, value)
            await session_ctx.commit()

    async def get_research_session(
        self, research_session_id: UUID
    ) -> ResearchSession | None:
        async with self._session() as session:
            row = await session.get(ResearchSessionRecord, str(research_session_id))
            return _research_session_from_row(row) if row else None

    async def list_research_sessions(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ResearchSession], int]:
        async with self._session() as session:
            stmt = select(ResearchSessionRecord)
            if status:
                stmt = stmt.where(ResearchSessionRecord.status == status)
            total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (
                (
                    await session.execute(
                        stmt.order_by(
                            ResearchSessionRecord.created_at.desc(),
                            ResearchSessionRecord.research_session_id.desc(),
                        )
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            return [_research_session_from_row(row) for row in rows], total or 0

    async def oldest_active_research_session(
        self, limit: int = 1
    ) -> list[ResearchSession]:
        """Oldest queued/running sessions, one session at a time (serial)."""
        async with self._session() as session:
            rows = (
                (
                    await session.execute(
                        select(ResearchSessionRecord)
                        .where(
                            ResearchSessionRecord.status.in_(
                                [
                                    ResearchSessionStatus.QUEUED.value,
                                    ResearchSessionStatus.RUNNING.value,
                                ]
                            )
                        )
                        .order_by(
                            ResearchSessionRecord.created_at.asc(),
                            ResearchSessionRecord.research_session_id.asc(),
                        )
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return [_research_session_from_row(row) for row in rows]

    async def cancel_dot_runs_for_session(self, research_session_id: UUID) -> int:
        """Stop every unfinished dot run of a session (queued or running)."""
        async with self._session() as session:
            rows = (
                (
                    await session.execute(
                        select(DotRunRecord).where(
                            DotRunRecord.session_id == str(research_session_id),
                            DotRunRecord.status.in_(
                                [DotRunStatus.QUEUED.value, DotRunStatus.RUNNING.value]
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                row.status = DotRunStatus.CANCELLED.value
            await session.commit()
            return len(rows)

    async def count_research_sessions(self, status: str | None = None) -> int:
        async with self._session() as session:
            stmt = select(func.count()).select_from(ResearchSessionRecord)
            if status:
                stmt = stmt.where(ResearchSessionRecord.status == status)
            return await session.scalar(stmt) or 0

    async def fail_stale_research_sessions(self) -> int:
        """Fail research sessions left RUNNING after a restart.

        Mirror of :meth:`fail_stale_dot_runs`: a session mid-flight when the
        process died can never finish (its in-memory plan is gone), so it is
        failed loudly instead of hanging in "running" forever.
        """
        async with self._session() as session:
            rows = (
                (
                    await session.execute(
                        select(ResearchSessionRecord)
                        .where(
                            ResearchSessionRecord.status
                            == ResearchSessionStatus.RUNNING.value
                        )
                        .limit(200)
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                row.status = ResearchSessionStatus.FAILED.value
                row.error = "interrupted by restart"
            await session.commit()
            return len(rows)
