"""Thin HTTP clients shared by observers and the Hermes publisher."""

from __future__ import annotations

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)


class Clients:
    """External HTTP surface injected into observers and publishers."""

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self.http = http

    @classmethod
    def defaults(cls, timeout: float = 5.0) -> Clients:
        return cls(
            http=httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                headers={"User-Agent": "anton-sentinel/1.0"},
            )
        )

    async def aclose(self) -> None:
        if self.http is not None:
            await self.http.aclose()
