from __future__ import annotations

from uuid import uuid4

from app.database.repository import Repository
from app.intelligence.extract import RawEntity
from app.intelligence.resolve import EntityResolver
from app.models.entity import EntityKind


async def test_resolve_creates_then_merges(repository: Repository) -> None:
    resolver = EntityResolver(repository)
    raw = RawEntity(name="evil.example.org", kind=EntityKind.DOMAIN, confidence=0.95)

    created, updated = await resolver.resolve([raw], evidence_id=uuid4(), source="test")
    assert len(created) == 1
    assert not updated

    created, updated = await resolver.resolve([raw], evidence_id=uuid4(), source="test")
    assert not created
    assert len(updated) == 1
    assert updated[0].entity_id == created[0].entity_id if created else True
    stored = updated[0]
    assert stored.times_seen == 2
    assert stored.first_seen <= stored.last_seen
