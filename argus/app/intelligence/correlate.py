"""Correlation — build the knowledge graph from co-occurrence.

When two entities appear in the same evidence item they establish (or
strengthen) an undirected ``co_occurs_with`` relation. The more items they
share, the higher the times-seen count and the confidence. This is the coarse
first pass; richer typed relations (``attributed_to``, ``linked_to``, ...)
can layer on top later.
"""

from __future__ import annotations

from uuid import UUID

from app.core.logging import get_logger
from app.database.repository import Repository
from app.models.base import utcnow
from app.models.entity import Entity, EntityRelation

log = get_logger(__name__)

RELATION_CO_OCCURS = "co_occurs_with"


def _ordered_pair(a: Entity, b: Entity) -> tuple[Entity, Entity]:
    """Stable (subject, object) ordering so dedup keys stay canonical."""
    if str(a.entity_id) <= str(b.entity_id):
        return a, b
    return b, a


class Correlator:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    async def correlate(self, per_item: list[tuple[UUID, list[Entity]]]) -> list[EntityRelation]:
        """Given (evidence_id, [entities]) pairs, upsert co-occurrence edges."""
        seen: dict[tuple[str, str, str], EntityRelation] = {}
        for evidence_id, entities in per_item:
            for index in range(len(entities)):
                for other in range(index + 1, len(entities)):
                    if entities[index].entity_id == entities[other].entity_id:
                        continue
                    await self._upsert(
                        seen,
                        *_ordered_pair(entities[index], entities[other]),
                        evidence_id=evidence_id,
                    )
        return list(seen.values())

    async def _upsert(
        self,
        seen: dict[tuple[str, str, str], EntityRelation],
        subject: Entity,
        object_: Entity,
        *,
        evidence_id: UUID,
    ) -> EntityRelation:
        key = (str(subject.entity_id), RELATION_CO_OCCURS, str(object_.entity_id))
        existing = seen.get(key)
        if existing is None:
            existing = await self._repository.get_relation(
                subject.entity_id, RELATION_CO_OCCURS, object_.entity_id
            )
        if existing is not None:
            merged = EntityRelation(
                relation_id=existing.relation_id,
                subject_id=existing.subject_id,
                relation=existing.relation,
                object_id=existing.object_id,
                evidence_ids=_append_unique(existing.evidence_ids, evidence_id),
                confidence=_strengthened_confidence(existing, subject, object_),
                first_seen=existing.first_seen,
                last_seen=utcnow(),
                times_seen=existing.times_seen + 1,
            )
        else:
            merged = EntityRelation(
                subject_id=subject.entity_id,
                relation=RELATION_CO_OCCURS,
                object_id=object_.entity_id,
                evidence_ids=[evidence_id],
                confidence=round((subject.confidence + object_.confidence) / 2, 4),
            )
        seen[key] = merged
        await self._repository.save_relation(merged)
        return merged


def _append_unique(values: list, value) -> list:
    if value in values:
        return values
    return [*values, value]


def _strengthened_confidence(existing: EntityRelation, a: Entity, b: Entity) -> float:
    base = (a.confidence + b.confidence) / 2
    boost = 0.05 * existing.times_seen
    return round(min(0.95, base + boost), 4)
