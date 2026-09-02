"""Thin HTTP clients shared by collectors and the Hermes publisher."""

from __future__ import annotations

import httpx


class Clients:
    """External HTTP surface injected into collectors and publishers."""

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self.http = http

    @classmethod
    def defaults(cls, timeout: float = 10.0) -> Clients:
        return cls(
            http=httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                headers={"User-Agent": "anton-argus/1.0"},
            )
        )

    async def aclose(self) -> None:
        if self.http is not None:
            await self.http.aclose()
