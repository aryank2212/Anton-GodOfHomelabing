from __future__ import annotations

import httpx
import pytest

from app.config.settings import Settings
from app.main import create_app


def _test_settings(tmp_path, *, command_token: str | None = None) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}",
        collectors="",
        hermes_enabled=False,
        oracle_enabled=False,
        dots_enabled=False,
        research_enabled=False,
        command_token=command_token,
    )


async def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.asyncio
async def test_mutating_route_requires_token_when_configured(tmp_path) -> None:
    app = create_app(_test_settings(tmp_path, command_token="sekret"))
    async with app.router.lifespan_context(app), await _client(app) as client:
        # read-only stays open with no token
        health = await client.get("/v1/health")
        assert health.status_code == 200

        # mutating without token -> 401
        no_auth = await client.post("/v1/dots", json={"topic": "unauthenticated run"})
        assert no_auth.status_code in (401, 403)

        # mutating with wrong token -> 403
        bad = await client.post(
            "/v1/dots",
            json={"topic": "wrong token run"},
            headers={"Authorization": "Bearer nope"},
        )
        assert bad.status_code == 403

        # mutating with correct token -> accepted (202) even though dots disabled
        ok = await client.post(
            "/v1/dots",
            json={"topic": "authenticated run"},
            headers={"Authorization": "Bearer sekret"},
        )
        assert ok.status_code in (202, 503)


@pytest.mark.asyncio
async def test_mutating_route_open_when_no_token_configured(tmp_path) -> None:
    app = create_app(_test_settings(tmp_path, command_token=None))
    async with app.router.lifespan_context(app), await _client(app) as client:
        health = await client.get("/v1/health")
        assert health.status_code == 200
        # no token configured -> the dependency short-circuits, never 401/403
        resp = await client.post("/v1/dots", json={"topic": "open run"})
        assert resp.status_code not in (401, 403)
