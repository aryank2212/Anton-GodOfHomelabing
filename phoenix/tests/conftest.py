from __future__ import annotations

from pathlib import Path

import pytest

from app.config.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# A minimal but valid Phoenix configuration for API tests. The scheduler is
# disabled by the test settings so no checks run against real services.
MINIMAL_CONFIG = """\
version: 1
scheduler:
  tick_interval: 3600
  max_concurrent_checks: 2

monitors:
  - name: web_http
    type: http
    interval: 60
    enabled: true
    severity: warning
    params:
      url: http://127.0.0.1:1/ping
      timeout: 1
      expected_status: 200

components:
  - name: web
    monitors: [web_http]
    recovery:
      strategy: http_retry
      params:
        url: http://127.0.0.1:1/ping
        timeout: 1
        expected_status: 200
      retry:
        attempts: 1
      escalate:
        severity: error
"""


def write_config(tmp_path: Path, content: str = MINIMAL_CONFIG) -> str:
    config_file = tmp_path / "phoenix.yaml"
    config_file.write_text(content, encoding="utf-8")
    return str(config_file)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings pointing at a throwaway database and config file."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]  # pydantic-settings init kwarg
        config_file=write_config(tmp_path),
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'phoenix.db'}",
        environment="test",
        hermes_enabled=False,
        scheduler_enabled=False,
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
