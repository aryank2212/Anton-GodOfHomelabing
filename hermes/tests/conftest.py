from __future__ import annotations

from pathlib import Path

import pytest
from app.config.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "app" / "templates"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Defaults pointing at a throwaway SQLite db and an empty rule set."""
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text("version: 1\nrules: []\n", encoding="utf-8")
    return Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'hermes.db'}",
        rules_file=str(rules_file),
        templates_dir=str(TEMPLATES_DIR),
        worker_concurrency=2,
        worker_sweep_interval=1.0,
        notification_max_attempts=2,
        notification_retry_base_delay=0.01,
    )


@pytest.fixture
async def app(settings):
    from app.main import create_app

    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
def state(app):
    return app.state
