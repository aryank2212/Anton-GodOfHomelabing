"""Report generator — distil confirmed hypotheses into intelligence reports.

A report is the end product of a pipeline pass: confirmed hypotheses plus the
evidence and entities that back them, structured into readable sections and
persisted for the Anton memory stack (and for Hermes to announce).
"""

from __future__ import annotations

from app.config.settings import Settings
from app.core.logging import get_logger
from app.database.repository import Repository
from app.models.hypothesis import Hypothesis, HypothesisStatus
from app.models.report import Report, ReportStatus

log = get_logger(__name__)


class ReportGenerator:
    def __init__(self, settings: Settings, repository: Repository) -> None:
        self._min_confidence = settings.report_min_confidence
        self._repository = repository

    async def generate(self, hypotheses: list[Hypothesis]) -> Report | None:
        """Build a report from confirmed, high-confidence hypotheses."""
        confirmed = [
            h
            for h in hypotheses
            if h.status == HypothesisStatus.CONFIRMED and h.confidence >= self._min_confidence
        ]
        if not confirmed:
            return None

        evidence_ids = _unique([e for h in confirmed for e in h.evidence_ids])
        entity_ids = _unique([e for h in confirmed for e in h.entity_ids])
        evidence = await self._load_evidence(evidence_ids)
        entities = await self._load_entities(entity_ids)

        sections = [
            {
                "key": "key_findings",
                "title": "Key findings",
                "items": [
                    {
                        "statement": h.statement,
                        "rationale": h.rationale,
                        "confidence": h.confidence,
                        "oracle_generated": h.oracle_generated,
                    }
                    for h in confirmed
                ],
            },
            {
                "key": "evidence",
                "title": "Supporting evidence",
                "items": [
                    {
                        "title": item.title,
                        "url": item.url,
                        "source": item.source,
                        "content_id": str(item.content_id),
                        "fetched_at": item.fetched_at.isoformat(),
                    }
                    for item in evidence
                ],
            },
            {
                "key": "entities",
                "title": "Entities involved",
                "items": [{"name": entity.name, "kind": entity.kind.value} for entity in entities],
            },
        ]

        summary = (
            f"Confirmed {len(confirmed)} finding(s) across {len(evidence)} item(s) "
            f"of evidence and {len(entities)} entity(ies)."
        )
        report = Report(
            title=f"Intelligence report — {confirmed[0].updated_at.date().isoformat()}",
            summary=summary,
            sections=sections,
            hypothesis_ids=[h.hypothesis_id for h in confirmed],
            evidence_ids=evidence_ids,
            entity_ids=entity_ids,
            status=ReportStatus.FINAL,
        )
        await self._repository.save_report(report)
        log.info(
            "report_generated",
            extra={
                "report_id": str(report.report_id),
                "hypotheses": len(confirmed),
                "evidence": len(evidence),
            },
        )
        return report

    async def _load_evidence(self, evidence_ids: list):
        loaded = []
        for evidence_id in evidence_ids:
            item = await self._repository.get_content(evidence_id)
            if item is not None:
                loaded.append(item)
        return loaded

    async def _load_entities(self, entity_ids: list):
        loaded = []
        for entity_id in entity_ids:
            entity = await self._repository.get_entity(entity_id)
            if entity is not None:
                loaded.append(entity)
        return loaded


def _unique(values: list) -> list:
    return list(dict.fromkeys(values))
