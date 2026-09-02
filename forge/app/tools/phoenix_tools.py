"""Phoenix tools: read Phoenix state and trigger its recovery workflow.

Phoenix is the autonomous recovery subsystem — it owns the incident database,
dependency cascade and per-component recovery strategies. Forge's
``phoenix_recover`` lets the agent ask Phoenix to run *its* recovery (instead
of blindly restarting a container), which is exactly the "Phoenix's recovery
actions" actuator surface.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.tools.base import Tool, ToolResult, ToolSpec

_COMPONENT_RE = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"


def _fmt(data: Any, indent: int = 2) -> str:
    try:
        return json.dumps(data, indent=indent, default=str)
    except (TypeError, ValueError):
        return str(data)


class _PhoenixClient:
    def __init__(self, base_url: str, timeout: float = 12.0) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "anton-forge/1.0"})

    async def close(self) -> None:
        await self._client.aclose()

    async def get(self, path: str) -> tuple[bool, str, Any]:
        try:
            response = await self._client.get(f"{self._base}{path}")
        except httpx.HTTPError as exc:
            return False, str(exc), None
        try:
            data = response.json()
        except ValueError:
            data = response.text
        return response.status_code < 400, response.text, data

    async def recover(self, component: str) -> tuple[bool, str, Any]:
        try:
            response = await self._client.post(f"{self._base}/recover/{component}")
        except httpx.HTTPError as exc:
            return False, str(exc), None
        try:
            data = response.json()
        except ValueError:
            data = response.text
        return response.status_code < 400, response.text, data


class PhoenixHealth(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="phoenix_health",
            description=(
                "Phoenix's own health: monitors, open incidents, scheduler "
                "state. Tells you what Phoenix already knows is wrong."
            ),
            risk="low",
            read_only=True,
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )

    def __init__(self, client: _PhoenixClient) -> None:
        self._client = client

    async def run(self, args: dict[str, Any]) -> ToolResult:
        ok, text, data = await self._client.get("/health")
        return ToolResult(ok=ok, output=text or _fmt(data), data=data)


class PhoenixIncidents(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="phoenix_incidents",
            description=(
                "List Phoenix incidents (newest first). Optional filters: "
                "component, status (open/resolved), severity. Use this before "
                "proposing recovery — Phoenix may already be recovering the "
                "component."
            ),
            risk="low",
            read_only=True,
            parameters={
                "type": "object",
                "properties": {
                    "component": {
                        "type": "string",
                        "description": "filter by component name",
                        "pattern": _COMPONENT_RE,
                    },
                    "status": {
                        "type": "string",
                        "description": "open or resolved",
                        "enum": ["open", "resolved"],
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "additionalProperties": False,
            },
        )

    def __init__(self, client: _PhoenixClient) -> None:
        self._client = client

    async def run(self, args: dict[str, Any]) -> ToolResult:
        params: list[str] = []
        if args.get("component"):
            params.append(f"component={args['component']}")
        if args.get("status"):
            params.append(f"status={args['status']}")
        params.append(f"limit={int(args.get('limit', 25))}")
        ok, text, data = await self._client.get(f"/incidents?{'&'.join(params)}")
        return ToolResult(ok=ok, output=text or _fmt(data), data=data)


class PhoenixRecover(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="phoenix_recover",
            description=(
                "Ask Phoenix to run its configured recovery for a component "
                "(restart strategy, retries, dependency cascade). Preferred "
                "over raw docker_restart when Phoenix knows the component. "
                "Returns the resulting incident. Medium risk."
            ),
            risk="medium",
            parameters={
                "type": "object",
                "properties": {
                    "component": {
                        "type": "string",
                        "description": "Phoenix component name",
                        "pattern": _COMPONENT_RE,
                    }
                },
                "required": ["component"],
                "additionalProperties": False,
            },
        )

    def __init__(self, client: _PhoenixClient) -> None:
        self._client = client

    def identity(self, args: dict[str, Any]) -> str:
        return str(args.get("component") or "?")

    async def run(self, args: dict[str, Any]) -> ToolResult:
        ok, text, data = await self._client.recover(args["component"])
        if not ok and data and isinstance(data, dict) and data.get("detail"):
            return ToolResult(
                ok=False, output=str(data["detail"]), data=data, error=str(data["detail"])
            )
        return ToolResult(ok=ok, output=text or _fmt(data), data=data)
