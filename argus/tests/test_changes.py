from __future__ import annotations

from uuid import uuid4

from app.database.repository import Repository
from app.intelligence.changes import ChangeDetector
from app.models.change import ChangeType
from app.models.content import ContentItem, SourceType, content_hash
from app.models.entity import Entity, EntityKind, EntityRelation


def _scrape_item(url: str, title: str, body: str) -> ContentItem:
    return ContentItem(
        source="scrape",
        source_type=SourceType.SCRAPE,
        url=url,
        title=title,
        body=body,
        content_hash=content_hash(url=url, title=title, body=body),
    )


async def test_content_change_detected(repository: Repository) -> None:
    detector = ChangeDetector(repository)
    url = "https://example.com/announcements"
    first = _scrape_item(url, "v1", "original")
    changed = _scrape_item(url, "v2", "updated")
    await repository.add_content(first)

    changes = await detector.detect([changed], [], [])
    assert len(changes) == 1
    change = changes[0]
    assert change.change_type == ChangeType.CONTENT_CHANGED
    assert change.object_key == f"url:{url}"
    assert change.before == {"content_hash": first.content_hash, "title": "v1"}
    assert change.after["title"] == "v2"


async def test_unchanged_scrape_produces_no_change(repository: Repository) -> None:
    detector = ChangeDetector(repository)
    url = "https://example.com/stable"
    first = _scrape_item(url, "v1", "same")
    await repository.add_content(first)

    same_again = _scrape_item(url, "v1", "same")
    changes = await detector.detect([same_again], [], [])
    assert changes == []


async def test_content_change_detected_after_ingest_ordering(repository: Repository) -> None:
    """Regression: ingest persists the item before the pass runs, so detect()
    must not compare a scrape against its own just-inserted record."""
    detector = ChangeDetector(repository)
    url = "https://example.com/regression"
    first = _scrape_item(url, "v1", "original")
    changed = _scrape_item(url, "v2", "updated")
    await repository.add_content(first)
    await repository.add_content(changed)

    changes = await detector.detect([changed], [], [])
    assert len(changes) == 1
    assert changes[0].change_type == ChangeType.CONTENT_CHANGED
    assert changes[0].before["title"] == "v1"
    assert changes[0].after["title"] == "v2"


def test_new_entity_change() -> None:
    detector = ChangeDetector.__new__(ChangeDetector)
    entity = Entity(kind=EntityKind.IP_ADDRESS, name="10.0.0.7", source="test")
    changes = detector._new_entities([entity])
    assert len(changes) == 1
    assert changes[0].change_type == ChangeType.NEW_ENTITY


def test_new_relation_change() -> None:
    detector = ChangeDetector.__new__(ChangeDetector)
    relation = EntityRelation(
        subject_id=uuid4(),
        relation="co_occurs_with",
        object_id=uuid4(),
        evidence_ids=[uuid4()],
        confidence=0.8,
    )
    changes = detector._new_relations([relation])
    assert len(changes) == 1
    assert changes[0].change_type == ChangeType.NEW_RELATION

    relation = relation.model_copy(update={"times_seen": 2})
    assert detector._new_relations([relation]) == []
