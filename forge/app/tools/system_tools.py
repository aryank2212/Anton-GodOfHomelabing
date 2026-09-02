"""Read-only context tools over Watcher and Hermes — cheap intel for the agent."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.tools.base import Tool, ToolResult, ToolSpec


class _ContextClient:
    def __init__(self, watcher_url: str, hermes_url: str, timeout: float = 12.0) -> None:
        self._watcher = watcher_url.rstrip("/")
        self._hermes = hermes_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "anton-forge/1.0"})

    async def close(self) -> None:
        await self._client.aclose()

    async def get(self, base: str, path: str) -> tuple[bool, str, Any]:
        try:
            response = await self._client.get(f"{base}{path}")
        except httpx.HTTPError as exc:
            return False, str(exc), None
        try:
            data = response.json()
        except ValueError:
            data = response.text
        return response.status_code < 400, response.text, data


class WatcherSummary(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="watcher_summary",
            description=(
                "WatchYourLAN fleet summary: online/total devices and the "
                "latest 50 (name, ip, mac, online). Compact and sized for AI "
                "context. Good for presence / LAN troubleshooting."
            ),
            risk="low",
            read_only=True,
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )

    def __init__(self, client: _ContextClient) -> None:
        self._client = client

    async def run(self, args: dict[str, Any]) -> ToolResult:
        ok, text, data = await self._client.get(self._client._watcher, "/summary")
        return ToolResult(ok=ok, output=text or json.dumps(data or {}), data=data)


class HermesEvents(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="hermes_events",
            description=(
                "Recent Hermes events (newest first). Use this to see the "
                "context that triggered an alert, including other services' "
                "failures."
            ),
            risk="low",
            read_only=True,
            parameters={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "severity": {
                        "type": "string",
                        "enum": ["debug", "info", "warning", "error", "critical"],
                    },
                },
                "additionalProperties": False,
            },
        )

    def __init__(self, client: _ContextClient) -> None:
        self._client = client

    async def run(self, args: dict[str, Any]) -> ToolResult:
        params = [f"limit={int(args.get('limit', 15))}"]
        if args.get("severity"):
            params.append(f"severity={args['severity']}")
        ok, text, data = await self._client.get(self._client._hermes, f"/events?{'&'.join(params)}")
        return ToolResult(ok=ok, output=text or json.dumps(data or {}), data=data)
