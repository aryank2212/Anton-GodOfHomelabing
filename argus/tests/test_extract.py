from __future__ import annotations

from app.config.settings import Settings
from app.intelligence.extract import EntityExtractor
from app.models.content import ContentItem, SourceType
from app.models.entity import EntityKind


def _item(body: str) -> ContentItem:
    return ContentItem(
        source="test",
        source_type=SourceType.RSS,
        title="digest",
        body=body,
    )


def test_extract_recognizes_technical_indicators(settings: Settings) -> None:
    body = (
        "CVE-2024-12345 affects hosts at 192.168.1.5. "
        "Report to security@example.com or visit https://example.com/advisory. "
        f"Sample hash {('a' * 64)} and domain example.org."
    )
    extractor = EntityExtractor(settings)
    found = extractor._deterministic(body)
    seen = {(entity.kind, entity.name) for entity in found}
    assert (EntityKind.CVE, "CVE-2024-12345") in seen
    assert (EntityKind.IP_ADDRESS, "192.168.1.5") in seen
    assert (EntityKind.EMAIL, "security@example.com") in seen
    assert (EntityKind.URL, "https://example.com/advisory") in seen
    assert (EntityKind.HASH, "a" * 64) in seen
    assert (EntityKind.DOMAIN, "example.org") in seen


async def test_extract_dedupes_and_scores(settings: Settings) -> None:
    body = "CVE-2024-9999 and again CVE-2024-9999"
    extractor = EntityExtractor(settings)
    found = await extractor.extract(_item(body))
    cves = [entity for entity in found if entity.kind == EntityKind.CVE]
    assert len(cves) == 1
    assert cves[0].name == "CVE-2024-9999"
    assert cves[0].confidence == 0.95


def test_entity_key_normalizes_names() -> None:
    from app.models.entity import entity_key, normalize_name

    assert normalize_name("  BlackCat   Group ") == "blackcat group"
    assert entity_key(EntityKind.THREAT_ACTOR, "  BlackCat   Group ") == (
        "threat_actor:blackcat group"
    )
