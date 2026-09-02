"""Entity and relation models — Argus' knowledge graph.

Entities are resolved names (people, organizations, IPs, domains, CVEs, ...)
that appear in collected evidence. Relations are directed edges between
entities, built from co-occurrence in evidence; the stronger the evidence, the
higher the confidence and the more times the edge has been seen.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.models.base import utcnow


class EntityKind(StrEnum):
    PERSON = "person"
    ORGANIZATION = "organization"
    LOCATION = "location"
    IP_ADDRESS = "ip_address"
    DOMAIN = "domain"
    URL = "url"
    EMAIL = "email"
    CVE = "cve"
    PRODUCT = "product"
    TECHNOLOGY = "technology"
    THREAT_ACTOR = "threat_actor"
    MALWARE = "malware"
    HASH = "hash"
    FILE = "file"
    OTHER = "other"


def normalize_name(name: str) -> str:
    """Lowercase, strip and collapse whitespace for canonical entity keys."""
    return " ".join(name.strip().lower().split())


def entity_key(kind: EntityKind | str, name: str) -> str:
    """Canonical storage key: ``<kind>:<normalized name>``."""
    return f"{EntityKind(kind).value}:{normalize_name(name)}"


class Entity(BaseModel):
    """A resolved, canonical entity in Argus' knowledge graph."""

    entity_id: UUID = Field(default_factory=uuid4)
    kind: EntityKind
    name: str = Field(min_length=1, max_length=256)
    aliases: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    first_seen: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)
    times_seen: int = Field(default=1, ge=1)
    source: str = Field(default="", max_length=64)

    @property
    def key(self) -> str:
        return entity_key(self.kind, self.name)

    def merge(self, other: Entity) -> Entity:
        """Fold another sighting of the same entity into this one."""
        return Entity(
            entity_id=self.entity_id,
            kind=self.kind,
            name=self.name,
            aliases=sorted(set([*self.aliases, *other.aliases])),
            attributes={**self.attributes, **other.attributes},
            confidence=round(max(self.confidence, other.confidence), 4),
            first_seen=min(self.first_seen, other.first_seen),
            last_seen=max(self.last_seen, other.last_seen),
            times_seen=self.times_seen + other.times_seen,
            source=self.source or other.source,
        )

    def to_record_dict(self) -> dict[str, Any]:
        return {
            "entity_id": str(self.entity_id),
            "kind": self.kind.value,
            "name": self.name,
            "name_key": entity_key(self.kind, self.name),
            "aliases": self.aliases,
            "attributes": self.attributes,
            "confidence": self.confidence,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "times_seen": self.times_seen,
            "source": self.source,
        }


class EntityRelation(BaseModel):
    """A directed edge between two entities, backed by evidence."""

    model_config = ConfigDict(frozen=True)

    relation_id: UUID = Field(default_factory=uuid4)
    subject_id: UUID
    relation: str = Field(min_length=1, max_length=64)
    object_id: UUID
    evidence_ids: list[UUID] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    first_seen: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)
    times_seen: int = Field(default=1, ge=1)

    @property
    def key(self) -> tuple[str, str, str]:
        """Order-independent dedup key for undirected relations."""
        a, b = sorted([str(self.subject_id), str(self.object_id)])
        return a, self.relation, b

    def to_record_dict(self) -> dict[str, Any]:
        return {
            "relation_id": str(self.relation_id),
            "subject_id": str(self.subject_id),
            "relation": self.relation,
            "object_id": str(self.object_id),
            "evidence_ids": [str(value) for value in self.evidence_ids],
            "confidence": self.confidence,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "times_seen": self.times_seen,
        }
