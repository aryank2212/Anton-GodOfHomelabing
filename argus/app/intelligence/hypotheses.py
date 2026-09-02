"""Hypothesis engine — turn evidence into testable claims.

Two generators feed it:

* **rule-based** (offline): a CVE co-occurring with a threat actor / malware
  sighting; a high-severity change (e.g. a tracked page changed).
* **Oracle** (optional): the LLM gateway proposes hypotheses from an evidence
  digest and they are parsed back into strict claims.

Every hypothesis is scored and persisted; high-confidence claims are promoted
to ``confirmed`` and feed the report generator.
"""

from __future__ import annotations

from typing import Any

from app.config.settings import Settings
from app.core.logging import get_logger
from app.core.oracle import OracleClient, OracleError
from app.models.change import Change, ChangeType
from app.models.content import ContentItem
from app.models.entity import EntityKind
from app.models.hypothesis import Hypothesis, HypothesisStatus

log = get_logger(__name__)

_ACTOR_KINDS = {EntityKind.THREAT_ACTOR, EntityKind.MALWARE, EntityKind.ORGANIZATION}

_CONFIRM_AT = 0.7
_SUPPORT_AT = 0.5


class HypothesisEngine:
    def __init__(
        self,
        settings: Settings,
        oracle: OracleClient | None = None,
    ) -> None:
        self._enabled = settings.hypothesis_enabled
        self._oracle_enabled = settings.oracle_enabled and settings.oracle_hypothesis_enabled
        self._oracle = oracle

    async def generate(
        self,
        *,
        items: list[ContentItem],
        entities_by_item: dict,
        changes: list[Change],
    ) -> list[Hypothesis]:
        """Generate, persist and return hypotheses for a batch of evidence."""
        if not self._enabled:
            return []
        hypotheses: list[Hypothesis] = []
        hypotheses.extend(self._rule_based(items, entities_by_item, changes))
        if self._oracle_enabled and self._oracle is not None:
            try:
                hypotheses.extend(await self._oracle_based(items))
            except OracleError as exc:
                log.warning("oracle_hypothesis_failed", extra={"error": str(exc)})
        return hypotheses

    # ------------------------------------------------------------ rule based
    def _rule_based(
        self,
        items: list[ContentItem],
        entities_by_item: dict,
        changes: list[Change],
    ) -> list[Hypothesis]:
        hypotheses: list[Hypothesis] = []
        hypotheses.extend(self._cve_actor(items, entities_by_item))
        hypotheses.extend(self._content_changed(changes))
        return hypotheses

    def _cve_actor(
        self,
        items: list[ContentItem],
        entities_by_item: dict,
    ) -> list[Hypothesis]:
        hypotheses: list[Hypothesis] = []
        for item in items:
            entities = entities_by_item.get(item.content_id, [])
            cves = [e for e in entities if e.kind == EntityKind.CVE]
            actors = [e for e in entities if e.kind in _ACTOR_KINDS]
            if not cves or not actors:
                continue
            names = ", ".join(sorted({e.name for e in cves}))
            actor_names = ", ".join(sorted({e.name for e in actors}))
            hypotheses.append(
                Hypothesis(
                    statement=(
                        f"{names} may be linked to activity attributed to " f"{actor_names}."
                    ),
                    rationale=(
                        f"Co-occurrence in '{item.title}' with confidence from "
                        f"{len(cves)} CVE and {len(actors)} actor sightings."
                    ),
                    evidence_ids=[item.content_id],
                    entity_ids=[e.entity_id for e in [*cves, *actors]],
                    confidence=round(min(0.9, 0.5 + 0.1 * len(actors)), 4),
                )
            )
        return hypotheses

    def _content_changed(self, changes: list[Change]) -> list[Hypothesis]:
        hypotheses: list[Hypothesis] = []
        for change in changes:
            if change.change_type != ChangeType.CONTENT_CHANGED:
                continue
            url = change.metadata.get("url")
            hypotheses.append(
                Hypothesis(
                    statement=f"Tracked page {url or change.object_key} has changed.",
                    rationale="A monitored site produced a different content hash.",
                    evidence_ids=[change.evidence_id] if change.evidence_id else [],
                    entity_ids=[],
                    confidence=0.6,
                )
            )
        return hypotheses

    # ------------------------------------------------------------ oracle
    async def _oracle_based(self, items: list[ContentItem]) -> list[Hypothesis]:
        assert self._oracle is not None
        digest = (
            "\n".join(f"- [{item.source}] {item.title}" for item in items[-20:]) or "(no evidence)"
        )
        raw_hypotheses = await self._oracle.generate_hypotheses(digest)
        hypotheses: list[Hypothesis] = []
        for raw in raw_hypotheses:
            statement = str(raw.get("statement") or "").strip()
            if not statement:
                continue
            confidence = _clamp(raw.get("confidence"), 0.3)
            hypotheses.append(
                Hypothesis(
                    statement=statement,
                    rationale=str(raw.get("rationale") or ""),
                    evidence_ids=[item.content_id for item in items],
                    confidence=confidence,
                    oracle_generated=True,
                )
            )
        return hypotheses


async def persist_and_promote(repository, hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    """Persist each hypothesis, promoting it by confidence, and return them."""
    saved: list[Hypothesis] = []
    for hypothesis in hypotheses:
        status = HypothesisStatus.PROPOSED
        if hypothesis.confidence >= _CONFIRM_AT:
            status = HypothesisStatus.CONFIRMED
        elif hypothesis.confidence >= _SUPPORT_AT:
            status = HypothesisStatus.SUPPORTED
        persisted = hypothesis.promote(status)
        await repository.save_hypothesis(persisted)
        saved.append(persisted)
    return saved


def _clamp(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))
