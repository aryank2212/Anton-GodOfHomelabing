"""Entity extraction — deterministic patterns plus optional Oracle enrichment.

The deterministic extractor is fast, offline and reproducible: it recognises
IPs, domains, emails, URLs, CVEs and file hashes straight from the text. When
Oracle is enabled, extraction is delegated to the LLM gateway for entity types
a regex cannot see (people, organizations, threat actors, malware, products).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from app.config.settings import Settings
from app.core.logging import get_logger
from app.core.oracle import OracleClient, OracleError
from app.models.content import ContentItem
from app.models.entity import EntityKind, normalize_name

log = get_logger(__name__)

IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
URL_RE = re.compile(r"\bhttps?://[A-Za-z0-9][-A-Za-z0-9./?%&=_#:]*\b")
DOMAIN_RE = re.compile(r"\b(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}\b")
CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)
SHA256_RE = re.compile(r"\b[a-f0-9]{64}\b")
SHA1_RE = re.compile(r"\b[a-f0-9]{40}\b")
MD5_RE = re.compile(r"\b[a-f0-9]{32}\b")

_ORACLE_KINDS = {kind.value for kind in EntityKind}

_DETERMINISTIC: list[tuple[EntityKind, re.Pattern[str]]] = [
    (EntityKind.CVE, CVE_RE),
    (EntityKind.IP_ADDRESS, IP_RE),
    (EntityKind.EMAIL, EMAIL_RE),
    (EntityKind.URL, URL_RE),
    (EntityKind.HASH, SHA256_RE),
    (EntityKind.HASH, SHA1_RE),
    (EntityKind.HASH, MD5_RE),
    (EntityKind.DOMAIN, DOMAIN_RE),
]


class RawEntity(BaseModel):
    """An as-yet-unresolved entity mention from an item."""

    name: str = Field(min_length=1, max_length=256)
    kind: EntityKind
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    aliases: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class EntityExtractor:
    def __init__(
        self,
        settings: Settings,
        oracle: OracleClient | None = None,
    ) -> None:
        self._oracle_enabled = settings.oracle_enabled and settings.oracle_extraction_enabled
        self._only_cves = settings.oracle_extraction_only_cves
        self._oracle = oracle

    async def extract(self, item: ContentItem) -> list[RawEntity]:
        """Extract raw entities from one evidence item."""
        text = f"{item.title}\n{item.body}"[:200_000]
        results: list[RawEntity] = list(self._deterministic(text))
        if self._oracle_enabled and self._oracle is not None:
            if self._only_cves and not CVE_RE.search(text):
                return results
            try:
                results.extend(await self._oracle_extract(text))
            except OracleError as exc:
                log.warning("oracle_extraction_failed", extra={"error": str(exc)})
        return _dedupe(results)

    def _deterministic(self, text: str) -> list[RawEntity]:
        results: list[RawEntity] = []
        for kind, pattern in _DETERMINISTIC:
            seen: set[str] = set()
            for match in pattern.findall(text):
                name = match.strip()
                if not name or normalize_name(name) in seen:
                    continue
                seen.add(normalize_name(name))
                results.append(
                    RawEntity(
                        name=name,
                        kind=kind,
                        confidence=0.95,
                    )
                )
        return results

    async def _oracle_extract(self, text: str) -> list[RawEntity]:
        assert self._oracle is not None
        raw_entities = await self._oracle.extract_entities(text)
        results: list[RawEntity] = []
        for raw in raw_entities:
            name = str(raw.get("name") or "").strip()
            kind_value = str(raw.get("kind") or "other")
            if not name or kind_value not in _ORACLE_KINDS:
                continue
            confidence = raw.get("confidence")
            try:
                parsed_confidence = float(confidence) if confidence is not None else 0.6
            except (TypeError, ValueError):
                parsed_confidence = 0.6
            aliases = raw.get("aliases")
            attributes = raw.get("attributes")
            results.append(
                RawEntity(
                    name=name,
                    kind=EntityKind(kind_value),
                    confidence=max(0.0, min(1.0, parsed_confidence)),
                    aliases=[str(alias) for alias in aliases] if isinstance(aliases, list) else [],
                    attributes=attributes if isinstance(attributes, dict) else {},
                )
            )
        return results


def _dedupe(entities: list[RawEntity]) -> list[RawEntity]:
    """Merge repeats by (kind, normalized name), keeping the best confidence."""
    best: dict[tuple[str, str], RawEntity] = {}
    for entity in entities:
        key = (entity.kind.value, normalize_name(entity.name))
        previous = best.get(key)
        if previous is None or entity.confidence > previous.confidence:
            best[key] = entity
        elif previous is not None:
            best[key] = previous.model_copy(
                update={"aliases": sorted(set([*previous.aliases, *entity.aliases]))}
            )
    return list(best.values())
