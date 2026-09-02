"""Observer interface — the common contract every data source implements.

Observers only *collect* information. They never make decisions, never act on
what they see and never publish anywhere: everything they perceive becomes an
immutable Observation that flows into the correlation and presence engines.

Adding a data source means:

1. Subclass ``Observer`` and implement ``collect``.
2. Register it in the observer registry (see ``app/observers/registry.py``).

``collect`` may raise; the scheduler guards it, records the error for
``/observers`` and keeps the loop alive.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from app.config.loader import ObserverSpec
from app.models.observation import Category, Observation, Severity


class Observer(ABC):
    """A single source of truth about one aspect of Anton."""

    name: str = "abstract"
    category: Category = Category.SYSTEM
    description: str = ""

    def __init__(
        self,
        spec: ObserverSpec | None = None,
        *,
        default_interval: float = 30.0,
        default_timeout: float = 10.0,
    ) -> None:
        spec = spec or ObserverSpec()
        self.enabled = spec.enabled
        self.interval = spec.interval if spec.interval is not None else default_interval
        self.timeout = spec.timeout if spec.timeout is not None else default_timeout

    async def setup(self) -> None:  # noqa: B027 - optional hook, defaults to no-op
        """Prepare resources. Runs once before the collect loop starts."""

    @abstractmethod
    async def collect(self) -> Sequence[Observation]:
        """Gather observations. Never raises (the scheduler catches anyway)."""

    async def shutdown(self) -> None:  # noqa: B027 - optional hook, defaults to no-op
        """Release resources."""

    def _observation(
        self,
        *,
        object: str,
        state: str,
        severity: Severity | str = Severity.INFO,
        confidence: float = 0.9,
        metadata: dict | None = None,
        tags: list[str] | None = None,
    ) -> Observation:
        return Observation(
            source=self.name,
            category=self.category,
            severity=Severity(severity) if isinstance(severity, str) else severity,
            object=object,
            state=state,
            confidence=confidence,
            metadata=metadata or {},
            tags=tags or [self.name],
        )
