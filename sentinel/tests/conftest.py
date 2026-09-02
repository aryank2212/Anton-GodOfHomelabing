"""Shared fixtures: isolated database, app, HTTP client and helpers."""

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
import pytest

from app.config.settings import Settings
from app.database.repository import Repository
from app.database.session import Database
from app.main import create_app
from app.models.observation import Category, Observation

CONFIG_DIR = Path(__file__).resolve().parent.parent / "app" / "config"


@pytest.fixture
def config_dir() -> Path:
    return CONFIG_DIR


@pytest.fixture
def settings_factory(config_dir):
    """Build isolated Settings objects, each with its own empty database."""

    def factory(**overrides):
        defaults = {
            "config_dir": str(config_dir),
            "observers": "",
            "hermes_enabled": False,
        }
        defaults.update(overrides)
        defaults.setdefault(
            "database_url",
            f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/sentinel-test.db",
        )
        return Settings(**defaults)

    return factory


@pytest.fixture
async def database(settings_factory):
    db = Database(settings_factory().database_url)
    await db.init()
    yield db
    await db.dispose()


@pytest.fixture
async def repository(database):
    return Repository(database.session_factory)


@pytest.fixture
async def app_and_runtime(settings_factory):
    """FastAPI app running its real lifespan on the test's event loop."""
    app = create_app(settings_factory())
    async with app.router.lifespan_context(app):
        yield app
    # lifespan teardown (runtime.stop) runs on exit


@pytest.fixture
async def api(app_and_runtime):
    transport = httpx.ASGITransport(app=app_and_runtime)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def observation(**overrides) -> Observation:
    """Build an Observation with sensible defaults for tests."""
    defaults = dict(
        source="test",
        category=Category.NETWORK,
        object="gateway",
        state="online",
        confidence=1.0,
    )
    defaults.update(overrides)
    return Observation(**defaults)
