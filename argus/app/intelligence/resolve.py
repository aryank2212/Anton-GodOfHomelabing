"""Entity resolution — fold raw mentions into canonical knowledge-graph nodes.

Raw entities extracted from evidence are matched against the store by their
canonical key (``<kind>:<normalized name>``). A match merges the new sighting
into the existing node (aliases, attributes, confidence, counts); a miss
creates a fresh node. The resolver returns every entity it touched so the
change detector can turn first sightings into ``new_entity`` changes.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.database.repository import Repository
from app.intelligence.extract import RawEntity
from app.models.entity import Entity, entity_key

log = get_logger(__name__)


class EntityResolver:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    async def resolve(
        self,
        raw_entities: list[RawEntity],
        *,
        evidence_id,
        source: str,
    ) -> tuple[list[Entity], list[Entity]]:
        """Resolve raw mentions against the store.

        Returns ``(created, updated)`` — the new nodes and the merged nodes —
        so callers can record ``new_entity`` changes and correlations.
        """
        created: list[Entity] = []
        updated: list[Entity] = []
        for raw in raw_entities:
            key = entity_key(raw.kind, raw.name)
            existing = await self._repository.get_entity_by_key(key)
            sighting = Entity(
                kind=raw.kind,
                name=raw.name,
                aliases=raw.aliases,
                attributes=raw.attributes,
                confidence=raw.confidence,
                source=source,
            )
            if existing is None:
                await self._repository.save_entity(sighting)
                created.append(sighting)
            else:
                merged = existing.merge(sighting)
                await self._repository.save_entity(merged)
                updated.append(merged)
        return created, updated
