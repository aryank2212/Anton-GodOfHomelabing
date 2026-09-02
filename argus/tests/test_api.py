from __future__ import annotations

import httpx
import pytest

from app.config.settings import Settings
from app.main import create_app
from app.models.content import ContentItem, SourceType


def _test_settings(tmp_path, **overrides) -> Settings:
    base = dict(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}",
        collectors="",
        hermes_enabled=False,
        oracle_enabled=False,
        # Explicit, so the developer's real .env (which may set a command
        # token) does not leak into these routing/schema tests.
        command_token="",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.mark.asyncio
async def test_health_and_evidence_flow(tmp_path) -> None:
    app = create_app(_test_settings(tmp_path))
    item = ContentItem(source="test", source_type=SourceType.RSS, title="first post", body="hello")

    async with app.router.lifespan_context(app):
        assert app.state.runtime.repository is not None
        await app.state.runtime.repository.add_content(item)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # Root serves the command-center SPA (the web build is committed).
            root = await client.get("/")
            assert root.status_code == 200
            assert "text/html" in root.headers.get("content-type", "")
            assert "ARGUS" in root.text

            web_asset = await client.get("/static/app.js")
            assert web_asset.status_code == 200
            assert "Argus command center" in web_asset.text

            health = await client.get("/health")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"

            health = await client.get("/v1/health")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"
            assert health.json()["evidence"] == 1

            evidence = await client.get("/v1/evidence")
            assert evidence.status_code == 200
            body = evidence.json()
            assert body["total"] == 1
            assert body["items"][0]["title"] == "first post"
            assert body["next_offset"] is None

            single = await client.get(f"/v1/evidence/{body['items'][0]['content_id']}")
            assert single.status_code == 200
            assert single.json()["source"] == "test"

            entities = await client.get("/v1/entities")
            assert entities.status_code == 200
            assert entities.json()["total"] == 0

            relations = await client.get("/v1/graph/relations")
            assert relations.status_code == 200
            assert relations.json()["total"] == 0

            changes = await client.get("/v1/changes")
            assert changes.status_code == 200
            assert changes.json()["total"] == 0

            sources = await client.get("/v1/sources")
            assert sources.status_code == 200
            assert sources.json()["running"] is False

            reports = await client.get("/v1/reports")
            assert reports.status_code == 200


@pytest.mark.asyncio
async def test_missing_evidence_is_404(tmp_path) -> None:
    app = create_app(_test_settings(tmp_path))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/v1/evidence/00000000-0000-0000-0000-000000000000")
            assert response.status_code == 404


@pytest.mark.asyncio
async def test_dots_status_filter_is_validated(tmp_path) -> None:
    app = create_app(_test_settings(tmp_path))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            bogus = await client.get("/v1/dots?status=bogus")
            assert bogus.status_code == 422

            valid = await client.get("/v1/dots?status=running")
            assert valid.status_code == 200
            assert valid.json()["total"] == 0

            no_filter = await client.get("/v1/dots")
            assert no_filter.status_code == 200


@pytest.mark.asyncio
async def test_dot_watch_crud(tmp_path) -> None:
    app = create_app(_test_settings(tmp_path))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            created = await client.post(
                "/v1/dots/watches", json={"topic": "scheduled topic", "interval_hours": 6}
            )
            assert created.status_code == 201
            body = created.json()
            watch_id = body["dot_watch_id"]
            assert body["topic"] == "scheduled topic"
            assert body["interval_hours"] == 6

            watches = await client.get("/v1/dots/watches")
            assert watches.status_code == 200
            assert watches.json()["total"] == 1

            patched = await client.patch(
                f"/v1/dots/watches/{watch_id}", json={"enabled": False, "iterations": 5}
            )
            assert patched.status_code == 200
            assert patched.json()["enabled"] is False
            assert patched.json()["iterations"] == 5

            one = await client.get(f"/v1/dots/watches/{watch_id}")
            assert one.status_code == 200
            assert one.json()["interval_hours"] == 6

            bad_provider = await client.post(
                "/v1/dots/watches", json={"topic": "x", "providers": ["nope"]}
            )
            assert bad_provider.status_code == 422

            empty_topic = await client.post(
                "/v1/dots/watches", json={"topic": "   ", "interval_hours": 1}
            )
            assert empty_topic.status_code == 422

            # dots engine disabled in tests -> run-now is unavailable (503).
            run_now = await client.post(f"/v1/dots/watches/{watch_id}/run")
            assert run_now.status_code == 503

            deleted = await client.delete(f"/v1/dots/watches/{watch_id}")
            assert deleted.status_code == 204

            gone = await client.get(f"/v1/dots/watches/{watch_id}")
            assert gone.status_code == 404


@pytest.mark.asyncio
async def test_dot_watch_unknown_returns_404(tmp_path) -> None:
    app = create_app(_test_settings(tmp_path))
    unknown = "00000000-0000-0000-0000-000000000000"
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            assert (await client.get(f"/v1/dots/watches/{unknown}")).status_code == 404
            assert (await client.patch(f"/v1/dots/watches/{unknown}", json={})).status_code == 404
            assert (await client.delete(f"/v1/dots/watches/{unknown}")).status_code == 404
            assert (await client.post(f"/v1/dots/watches/{unknown}/run")).status_code == 404

@pytest.mark.asyncio
async def test_research_structured_target(tmp_path) -> None:
    # research worker is only built when dots_enabled AND oracle are present.
    app = create_app(_test_settings(tmp_path, oracle_enabled=True))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            body = {
                "target": {
                    "target": "Acme Corp",
                    "place": "Singapore",
                    "date_from": "2026-01-01",
                    "date_to": "2026-06-30",
                    "keywords": ["IPO", "layoffs"],
                }
            }
            created = await client.post("/v1/research/sessions", json=body)
            assert created.status_code == 202
            data = created.json()
            assert "Acme Corp" in data["question"]
            assert "Singapore" in data["question"]
            assert "IPO" in data["question"]
            assert data["metadata"]["target"]["target"] == "Acme Corp"
            assert data["metadata"]["target"]["keywords"] == ["IPO", "layoffs"]

            # A request with neither question nor target is rejected.
            bad = await client.post("/v1/research/sessions", json={})
            assert bad.status_code == 422

            # Target-only session is allowed when a target is present.
            ok = await client.post(
                "/v1/research/sessions",
                json={"target": {"target": "OnlyTarget"}},
            )
            assert ok.status_code == 202
