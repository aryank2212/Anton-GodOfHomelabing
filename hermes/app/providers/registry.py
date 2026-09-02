from __future__ import annotations

from app.config.settings import Settings
from app.core.logging import get_logger
from app.providers.base import BaseProvider
from app.providers.discord import DiscordProvider
from app.providers.email import EmailProvider
from app.providers.ntfy import NtfyProvider
from app.providers.telegram import TelegramProvider
from app.providers.webhook import WebhookProvider

log = get_logger(__name__)


class ProviderRegistry:
    """Holds every registered provider and resolves rule targets.

    Rules address providers by ``name``. ``"all"`` expands to every provider
    that is currently enabled. Unknown or disabled providers are skipped with
    a warning instead of failing the whole dispatch.
    """

    def __init__(self, settings: Settings) -> None:
        self._providers: dict[str, BaseProvider] = {}
        for provider in (
            DiscordProvider(settings),
            TelegramProvider(settings),
            EmailProvider(settings),
            NtfyProvider(settings),
            WebhookProvider(settings),
        ):
            self._providers[provider.name] = provider

    @property
    def enabled(self) -> list[BaseProvider]:
        return [provider for provider in self._providers.values() if provider.enabled]

    @property
    def names(self) -> list[str]:
        return list(self._providers)

    def register(self, provider: BaseProvider) -> None:
        """Register an additional provider (used by tests and future modules)."""
        self._providers[provider.name] = provider

    def get(self, name: str) -> BaseProvider | None:
        return self._providers.get(name)

    def resolve(self, names: list[str]) -> list[BaseProvider]:
        resolved: list[BaseProvider] = []
        for name in names:
            if name == "all":
                resolved.extend(self.enabled)
                continue
            provider = self._providers.get(name)
            if provider is None:
                log.warning("unknown_provider_requested", extra={"provider": name})
                continue
            if provider.enabled:
                resolved.append(provider)
            else:
                log.warning("provider_disabled_skipping", extra={"provider": name})
        return resolved

    async def close(self) -> None:
        for provider in self._providers.values():
            await provider.close()
