"""Change detection — surface what changed in the monitored world.

Discrete facts the rest of the stack can act on:

* a tracked page changed (same URL, different content hash),
* an entity appeared for the first time,
* a new relation was established between entities.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.database.repository import Repository
from app.models.change import Change, ChangeType
from app.models.content import ContentItem, Severity, SourceType
from app.models.entity import Entity, EntityRelation

log = get_logger(__name__)


class ChangeDetector:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    async def detect(
        self,
        items: list[ContentItem],
        created_entities: list[Entity],
        new_relations: list[EntityRelation],
    ) -> list[Change]:
        """Detect changes introduced by a batch of evidence."""
        changes: list[Change] = []
        changes.extend(await self._content_changes(items))
        changes.extend(self._new_entities(created_entities))
        changes.extend(self._new_relations(new_relations))
        return changes

    async def _content_changes(self, items: list[ContentItem]) -> list[Change]:
        changes: list[Change] = []
        for item in items:
            if not item.url or item.source_type != SourceType.SCRAPE:
                continue
            previous = await self._repository.latest_for_url(item.url, exclude_id=item.content_id)
            if (
                previous is None
                or previous.content_id == item.content_id
                or previous.content_hash == item.content_hash
            ):
                continue
            changes.append(
                Change(
                    change_type=ChangeType.CONTENT_CHANGED,
                    object_key=f"url:{item.url}",
                    attribute="content_hash",
                    before={"content_hash": previous.content_hash, "title": previous.title},
                    after={"content_hash": item.content_hash, "title": item.title},
                    evidence_id=item.content_id,
                    severity=Severity.MEDIUM,
                    confidence=0.9,
                    metadata={"url": item.url},
                )
            )
        return changes

    def _new_entities(self, created: list[Entity]) -> list[Change]:
        changes: list[Change] = []
        for entity in created:
            changes.append(
                Change(
                    change_type=ChangeType.NEW_ENTITY,
                    object_key=entity.key,
                    attribute="name",
                    after={"name": entity.name, "kind": entity.kind.value},
                    severity=Severity.INFO,
                    confidence=entity.confidence,
                    metadata={"entity_id": str(entity.entity_id)},
                )
            )
        return changes

    def _new_relations(self, relations: list[EntityRelation]) -> list[Change]:
        changes: list[Change] = []
        for relation in relations:
            if relation.times_seen > 1:
                continue
            changes.append(
                Change(
                    change_type=ChangeType.NEW_RELATION,
                    object_key=(f"{relation.subject_id}-{relation.relation}-{relation.object_id}"),
                    attribute="relation",
                    after={
                        "subject_id": str(relation.subject_id),
                        "relation": relation.relation,
                        "object_id": str(relation.object_id),
                    },
                    severity=Severity.LOW,
                    confidence=relation.confidence,
                    metadata={"relation_id": str(relation.relation_id)},
                )
            )
        return changes
