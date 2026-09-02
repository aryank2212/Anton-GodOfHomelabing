"""Collector registry — maps source names to implementations.

Built-in collectors: ``rss``, ``scrape``, ``osint``, ``telegram``.

To add a collector type: subclass ``Collector`` and register a factory (see
``default_registry``). No other code changes are needed.
"""

from __future__ import annotations

from collections.abc import Callable

from app.config.loader import SourcesConfig, SourceSpec
from app.config.settings import Settings
from app.core.clients import Clients
from app.core.logging import get_logger
from app.sources.base import Collector

log = get_logger(__name__)

Factory = Callable[[SourceSpec, Settings, Clients], Collector]


class CollectorRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Factory] = {}

    def register(self, name: str, factory: Factory) -> None:
        self._factories[name] = factory

    def supports(self, name: str) -> bool:
        return name in self._factories

    def build_all(
        self,
        config: SourcesConfig,
        settings: Settings,
        clients: Clients,
    ) -> list[Collector]:
        """Build every enabled source, respecting the global env filter.

        ``ARGUS_COLLECTORS`` is a comma-separated list of collector *kinds*
        (rss, scrape, osint, telegram); a source is built only when its
        ``kind`` is present in that list. An empty list disables everything.
        """
        collectors: list[Collector] = []
        for name, spec in config.sources.items():
            kind = spec.kind
            if kind not in self._factories:
                log.warning("collector_kind_unknown", extra={"source": name, "kind": kind})
                continue
            if kind not in settings.enabled_collector_names:
                log.debug("collector_filtered", extra={"source": name, "kind": kind})
                continue
            factory = self._factories[kind]
            try:
                collector = factory(spec, settings, clients)
            except Exception as exc:  # noqa: BLE001 - never kill startup
                log.error("collector_build_failed", extra={"source": name, "error": str(exc)})
                continue
            if not collector.enabled:
                log.debug("collector_disabled", extra={"source": name})
                continue
            collectors.append(collector)
        return collectors


def default_registry() -> CollectorRegistry:
    """Registry with all built-in collector types."""
    from app.sources.osint import build_osint_collector
    from app.sources.rss import build_rss_collector
    from app.sources.scrape import build_scrape_collector
    from app.sources.telegram import build_telegram_collector

    registry = CollectorRegistry()
    registry.register("rss", build_rss_collector)
    registry.register("scrape", build_scrape_collector)
    registry.register("osint", build_osint_collector)
    registry.register("telegram", build_telegram_collector)
    return registry
