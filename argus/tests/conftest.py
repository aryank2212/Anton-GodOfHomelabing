from __future__ import annotations

import pytest

from app.config.settings import Settings
from app.database.repository import Repository
from app.database.session import Database


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
async def repository(tmp_path):
    """A Repository over a throwaway file-based SQLite database."""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'argus_test.db'}")
    await database.init()
    yield Repository(database.session_factory)
    await database.dispose()
