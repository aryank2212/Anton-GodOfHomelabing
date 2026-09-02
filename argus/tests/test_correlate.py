from __future__ import annotations

from uuid import uuid4

from app.database.repository import Repository
from app.intelligence.correlate import Correlator
from app.models.entity import Entity, EntityKind


async def test_correlate_upserts_and_strengthens(repository: Repository) -> None:
    correlator = Correlator(repository)
    cve = Entity(kind=EntityKind.CVE, name="CVE-2024-9999", confidence=0.9)
    actor = Entity(kind=EntityKind.THREAT_ACTOR, name="BlackCat", confidence=0.8)

    first = await correlator.correlate([(uuid4(), [cve, actor])])
    assert len(first) == 1
    assert first[0].times_seen == 1

    second = await correlator.correlate([(uuid4(), [cve, actor])])
    assert len(second) == 1
    assert second[0].relation_id == first[0].relation_id
    assert second[0].times_seen == 2
    assert len(second[0].evidence_ids) == 2
    assert second[0].confidence > first[0].confidence
