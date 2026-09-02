"""Programmatic Alembic runner — ``Database.init`` applies schema migrations.

Schema changes are versioned under ``alembic/versions``. A fresh database is
upgraded from empty; a *versioned* database is upgraded incrementally to head.
A pre-migration database (tables created by ``create_all``, no alembic
version) is adopted: stamped at the base revision — the snapshot its schema
matches — and then upgraded forward to head.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from alembic import command
from app.core.logging import get_logger

log = get_logger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_DIR = _ROOT / "alembic"
_INI_PATH = _ROOT / "alembic.ini"


def _config(database_url: str) -> Config:
    config = Config(str(_INI_PATH))
    config.set_main_option("script_location", str(_ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def base_revision() -> str | None:
    """The first migration in the chain (the create_all-era snapshot)."""
    directory = ScriptDirectory(str(_ALEMBIC_DIR))
    for script in directory.walk_revisions():
        if script.down_revision is None:
            return script.revision
    return None


def upgrade(database_url: str) -> None:
    command.upgrade(_config(database_url), "head")


def upgrade_to(database_url: str, revision: str) -> None:
    command.upgrade(_config(database_url), revision)


def stamp_head(database_url: str) -> None:
    command.stamp(_config(database_url), "head")


def stamp_and_upgrade(database_url: str) -> None:
    """Adopt an un-versioned pre-migration database, then walk it to head."""
    base = base_revision()
    if base is None:
        raise RuntimeError("no base migration revision is defined")
    command.stamp(_config(database_url), base)
    command.upgrade(_config(database_url), "head")
