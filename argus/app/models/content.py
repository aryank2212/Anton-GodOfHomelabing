"""ContentItem — the atomic unit of Argus' evidence.

Every piece of information a collector gathers from the internet becomes one
immutable ContentItem. The intelligence layer reads these records; collectors
only ever create them. ``content_hash`` is a fingerprint of the item used for
deduplication and change detection.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.models.base import utcnow


class Severity(StrEnum):
    """Five-step scale so thresholds stay simple."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SourceType(StrEnum):
    """Where an item came from on the internet."""

    RSS = "rss"
    SCRAPE = "scrape"
    OSINT = "osint"
    TELEGRAM = "telegram"
    DOTS = "dots"


def content_hash(*, url: str | None, title: str, body: str) -> str:
    """Stable fingerprint for deduplication and change detection.

    Scrape collectors pass the *normalized* body so the same page changed on
    disk still fingerprints as a new hash (that is how change detection sees
    it) while pure re-fetches with identical bytes dedupe away.
    """
    return hashlib.sha1(f"{url or ''}|{title}|{body}".encode()).hexdigest()


class ContentItem(BaseModel):
    """An immutable, standardized record of something Argus collected."""

    model_config = ConfigDict(frozen=True)

    content_id: UUID = Field(default_factory=uuid4)
    source: str = Field(min_length=1, max_length=64)
    source_type: SourceType
    url: str | None = Field(default=None, max_length=2048)
    title: str = Field(min_length=1, max_length=512)
    body: str = Field(default="", max_length=200_000)
    content_hash: str = Field(default="", max_length=64)
    language: str | None = Field(default=None, max_length=16)
    fetched_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)

    def to_record_dict(self) -> dict[str, Any]:
        """Flatten into the SQLAlchemy column layout."""
        return {
            "content_id": str(self.content_id),
            "source": self.source,
            "source_type": self.source_type.value,
            "url": self.url,
            "title": self.title,
            "body": self.body,
            "content_hash": self.content_hash,
            "language": self.language,
            "fetched_at": self.fetched_at,
            "metadata": self.metadata,
            "tags": self.tags,
        }
