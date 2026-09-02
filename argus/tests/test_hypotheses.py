from __future__ import annotations

from app.config.settings import Settings
from app.database.repository import Repository
from app.intelligence.hypotheses import HypothesisEngine, persist_and_promote
from app.models.change import Change, ChangeType
from app.models.content import ContentItem, Severity, SourceType
from app.models.entity import Entity, EntityKind
from app.models.hypothesis import Hypothesis, HypothesisStatus


def _item(title: str = "APT group exploits CVE-2024-9999") -> ContentItem:
    return ContentItem(source="feed", source_type=SourceType.RSS, title=title, body=title)


async def test_cve_actor_rule_supported(repository: Repository) -> None:
    engine = HypothesisEngine(Settings())
    item = _item()
    cve = Entity(kind=EntityKind.CVE, name="CVE-2024-9999", confidence=0.95)
    actor = Entity(kind=EntityKind.THREAT_ACTOR, name="BlackCat", confidence=0.8)

    hypotheses = await engine.generate(
        items=[item],
        entities_by_item={item.content_id: [cve, actor]},
        changes=[],
    )
    assert len(hypotheses) == 1
    assert hypotheses[0].confidence == 0.6

    saved = await persist_and_promote(repository, hypotheses)
    assert saved[0].status == HypothesisStatus.SUPPORTED


async def test_high_confidence_promotes_to_confirmed(repository: Repository) -> None:
    hypothesis = Hypothesis(
        statement="Evidence suggests a coordinated campaign.",
        rationale="Multiple sightings.",
        evidence_ids=[_item().content_id],
        confidence=0.75,
    )
    saved = await persist_and_promote(repository, [hypothesis])
    assert saved[0].status == HypothesisStatus.CONFIRMED


async def test_content_change_rule() -> None:
    engine = HypothesisEngine(Settings())
    change = Change(
        change_type=ChangeType.CONTENT_CHANGED,
        object_key="url:https://example.com/page",
        attribute="content_hash",
        severity=Severity.MEDIUM,
        confidence=0.9,
        metadata={"url": "https://example.com/page"},
    )
    hypotheses = await engine.generate(items=[], entities_by_item={}, changes=[change])
    assert len(hypotheses) == 1
    assert "changed" in hypotheses[0].statement.lower()


async def test_engine_can_be_disabled() -> None:
    settings = Settings(hypothesis_enabled=False)
    engine = HypothesisEngine(settings)
    item = _item()
    hypotheses = await engine.generate(
        items=[item],
        entities_by_item={item.content_id: []},
        changes=[],
    )
    assert hypotheses == []
