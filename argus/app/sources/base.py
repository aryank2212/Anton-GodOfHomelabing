"""Collector interface — the common contract every internet source implements.

Collectors only *collect* information from the internet. They never make
decisions, never act on what they see and never publish anywhere: everything
they gather becomes an immutable ContentItem (evidence) that flows into the
intelligence layer.

Adding a source type means:

1. Subclass ``Collector`` and implement ``collect``.
2. Register it in the collector registry (see ``app/sources/registry.py``).

``collect`` may raise; the scheduler guards it, records the error for
``/sources`` and keeps the loop alive.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from app.config.loader import SourceSpec
from app.models.content import ContentItem, SourceType, content_hash


class Collector(ABC):
    """A single source of information about the internet."""

    name: str = "abstract"
    source_type: SourceType = SourceType.RSS
    description: str = ""

    def __init__(
        self,
        spec: SourceSpec | None = None,
        *,
        default_interval: float = 900.0,
        default_timeout: float = 30.0,
    ) -> None:
        spec = spec or SourceSpec()
        self.enabled = spec.enabled
        self.interval = spec.interval if spec.interval is not None else default_interval
        self.timeout = spec.timeout if spec.timeout is not None else default_timeout

    async def setup(self) -> None:  # noqa: B027 - optional hook, defaults to no-op
        """Prepare resources. Runs once before the collect loop starts."""

    @abstractmethod
    async def collect(self) -> Sequence[ContentItem]:
        """Gather content items. Never raises (the scheduler catches anyway)."""

    async def shutdown(self) -> None:  # noqa: B027 - optional hook, defaults to no-op
        """Release resources."""

    def _item(
        self,
        *,
        url: str | None,
        title: str,
        body: str = "",
        language: str | None = None,
        metadata: dict | None = None,
        tags: list[str] | None = None,
    ) -> ContentItem:
        return ContentItem(
            source=self.name,
            source_type=self.source_type,
            url=url,
            title=title,
            body=body,
            content_hash=content_hash(url=url, title=title, body=body),
            language=language,
            metadata=metadata or {},
            tags=tags or [self.name],
        )
