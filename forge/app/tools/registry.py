"""Tool registry: builds every Tool instance and exposes specs to callers."""

from __future__ import annotations

from typing import Any

from app.clients.docker import DockerGateway
from app.config import Settings
from app.policy import Policy
from app.state import StateManager
from app.tools.base import Tool
from app.tools.docker_tools import (
    DockerInspect,
    DockerLogs,
    DockerMarkGood,
    DockerPs,
    DockerRestart,
    DockerRollback,
    DockerScale,
    DockerStart,
    DockerStats,
    DockerStop,
)
from app.tools.git_tools import GitLog, GitMarkGood, GitRollback, GitStatus
from app.tools.phoenix_tools import PhoenixHealth, PhoenixIncidents, PhoenixRecover, _PhoenixClient
from app.tools.research_tools import FetchUrl, WebSearch, WriteNote
from app.tools.system_tools import HermesEvents, WatcherSummary, _ContextClient


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools: dict[str, Tool] = {tool.spec.name: tool for tool in tools}

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        return [tool.spec.to_out().model_dump() for tool in self._tools.values()]

    def read_only_names(self) -> set[str]:
        return {tool.spec.name for tool in self._tools.values() if tool.spec.read_only}


class RegistryFactory:
    """Builds the live registry from settings + policy + state.

    External clients are created here and owned by the application; tests can
    instead build a registry from fakes.
    """

    def __init__(
        self,
        settings: Settings,
        policy: Policy,
        state: StateManager,
        *,
        docker: DockerGateway | None = None,
        phoenix: _PhoenixClient | None = None,
        context: _ContextClient | None = None,
    ) -> None:
        self._settings = settings
        self._policy = policy
        self._state = state
        self._docker = docker or DockerGateway(settings.docker_timeout)
        self._phoenix = phoenix or _PhoenixClient(settings.phoenix_url, settings.http_timeout)
        self._context = context or _ContextClient(
            settings.watcher_url, settings.hermes_url, settings.http_timeout
        )
        self._clients: list[Any] = [self._phoenix, self._context]
        self._research: list[Any] = []

    def build(self) -> ToolRegistry:
        web_search = WebSearch(
            max_results=self._settings.search_max_results,
            timeout=self._settings.search_timeout,
        )
        fetch_url = FetchUrl(
            max_bytes=self._settings.fetch_max_bytes,
            timeout=self._settings.fetch_timeout,
        )
        self._research.extend([fetch_url])
        return ToolRegistry(
            [
                DockerPs(self._docker),
                DockerInspect(self._docker),
                DockerLogs(self._docker),
                DockerStats(self._docker),
                DockerRestart(self._docker),
                DockerStart(self._docker),
                DockerStop(self._docker),
                DockerMarkGood(self._docker, self._state),
                DockerRollback(self._docker, self._state),
                DockerScale(self._policy),
                PhoenixHealth(self._phoenix),
                PhoenixIncidents(self._phoenix),
                PhoenixRecover(self._phoenix),
                GitStatus(self._policy, self._state),
                GitLog(self._policy, self._state),
                GitMarkGood(self._policy, self._state),
                GitRollback(self._policy, self._state),
                WatcherSummary(self._context),
                HermesEvents(self._context),
                web_search,
                fetch_url,
                WriteNote(self._settings.notes_dir),
            ]
        )

    async def close(self) -> None:
        for client in self._clients:
            await client.close()
        for tool in self._research:
            await tool.close()
