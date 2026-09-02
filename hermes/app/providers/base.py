from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx

from app.config.settings import Settings
from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class ProviderMessage:
    """Everything a provider needs to deliver one notification."""

    event_id: str
    event: dict[str, Any]
    rendered: dict[str, str]


class ProviderError(Exception):
    """Raised by a provider when delivery fails (expected error path)."""


class BaseProvider(ABC):
    """Interface every notification provider implements.

    Adding a new provider is a single class: subclass this, declare ``name``
    and ``templates``, implement ``enabled`` and ``send``, then register the
    class in :class:`app.providers.registry.ProviderRegistry`.
    """

    #: Unique, rule-addressable name (used in rule `providers`).
    name: str = ""

    #: Mapping of message slot -> template file, rendered by the Renderer.
    templates: ClassVar[dict[str, str]] = {}

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=10.0)

    @property
    def enabled(self) -> bool:
        """Whether the provider has everything it needs to be usable."""
        return True

    @abstractmethod
    async def send(self, message: ProviderMessage) -> None:
        """Deliver ``message``. Must raise ProviderError on failure.

        Implementations are allowed to be asynchronous; blocking work (such as
        SMTP) should be offloaded to an executor.
        """

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
