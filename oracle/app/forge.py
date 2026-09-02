from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings


class ForgeError(Exception):
    """Raised when Forge cannot be reached or rejects a request."""


class ForgeClient:
    """Async client for the Forge execution layer on the Anton host.

    Forge runs the actual container / git / system tools and enforces its own
    policy and Level-1 approvals. This client never decides what is allowed —
    it only relays the model's tool calls over Tailscale. Every request carries
    the shared bearer token.
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=settings.forge_timeout)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._settings.forge_token or ''}"}

    async def list_tools(self) -> list[dict[str, Any]]:
        """Fetch the Forge tool catalog advertised to AI callers."""
        response = await self._get("/v1/tools")
        data = response.json()
        tools = data.get("tools") if isinstance(data, dict) else None
        if not isinstance(tools, list):
            raise ForgeError("forge returned a malformed tool catalog")
        return tools

    async def run(self, tool: str, args: dict[str, Any], *, reason: str = "") -> dict[str, Any]:
        """Relay one tool call to Forge. Returns the raw /v1/run response."""
        payload: dict[str, Any] = {"tool": tool, "args": args, "reason": reason}
        try:
            response = await self._client.post(
                f"{self._settings.forge_url.rstrip('/')}/v1/run",
                json=payload,
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise ForgeError(f"forge request failed: {exc}") from exc
        if response.status_code >= 400:
            raise ForgeError(
                f"forge rejected the call ({response.status_code}): {self._detail(response)}"
            )
        return response.json()

    async def _get(self, path: str) -> httpx.Response:
        try:
            response = await self._client.get(
                f"{self._settings.forge_url.rstrip('/')}{path}", headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise ForgeError(f"forge request failed: {exc}") from exc
        if response.status_code >= 400:
            raise ForgeError(
                f"forge rejected the request ({response.status_code}): {self._detail(response)}"
            )
        return response

    @staticmethod
    def _detail(response: httpx.Response) -> str:
        try:
            body = response.json()
            if isinstance(body, dict) and body.get("detail"):
                return str(body["detail"])
        except ValueError:
            pass
        return response.text[:200]

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
