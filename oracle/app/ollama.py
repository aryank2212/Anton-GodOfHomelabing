from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import Settings


class OllamaError(Exception):
    """Raised when Ollama cannot produce a completion."""


class OllamaClient:
    """Thin async client over Ollama's chat API."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=settings.request_timeout)

    async def chat(
        self, messages: list[dict[str, str]], *, temperature: float | None = None
    ) -> tuple[str, dict[str, Any]]:
        """Run a chat completion. Returns (content, metadata)."""
        payload: dict[str, Any] = {
            "model": self._settings.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": (self._settings.temperature if temperature is None else temperature)
            },
        }
        started = time.monotonic()
        try:
            response = await self._client.post(
                f"{self._settings.ollama_url.rstrip('/')}/api/chat", json=payload
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OllamaError(f"ollama request failed: {exc}") from exc
        content = ((data.get("message") or {}).get("content") or "").strip()
        if not content:
            raise OllamaError("ollama returned an empty completion")
        metadata = {
            "model": data.get("model") or self._settings.model,
            "tokens": {
                "prompt": int(data.get("prompt_eval_count") or 0),
                "completion": int(data.get("eval_count") or 0),
            },
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
        return content, metadata

    async def is_up(self) -> bool:
        try:
            response = await self._client.get(f"{self._settings.ollama_url.rstrip('/')}/api/tags")
            return response.status_code < 400
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
