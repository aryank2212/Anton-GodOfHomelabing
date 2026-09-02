"""Tests for schema bootstrapping (alembic) and restart-recovery sweeps."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect

from app.database.session import Database
from app.models.dots import DotRun, DotRunStatus

_ALL_TABLES = {
    "changes",
    "dot_batches",
    "dot_runs",
    "dot_watches",
    "entities",
    "entity_relations",
    "evidence",
    "hypotheses",
    "reports",
    "research_sessions",
}


async def _tables(database: Database) -> set[str]:
    async with database.engine.connect() as connection:

        def read(sync_conn) -> set[str]:
            return set(inspect(sync_conn).get_table_names())

        return await connection.run_sync(read)


@pytest.mark.asyncio
async def test_fresh_database_is_created_by_migrations(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    await database.init()
    names = await _tables(database)
    assert names >= _ALL_TABLES
    # the dead investigations table is gone for good
    assert "investigations" not in names
    await database.dispose()


@pytest.mark.asyncio
async def test_versioned_database_is_upgraded_incrementally(tmp_path) -> None:
    """A database tracked by alembic at an old revision walks forward to head."""
    import asyncio

    from sqlalchemy import text

    from app.database import migrations

    legacy = tmp_path / "versioned.db"
    url = f"sqlite+aiosqlite:///{legacy}"
    # land the database at the initial snapshot revision only.
    await asyncio.to_thread(migrations.upgrade_to, url, "6d095cdffa5f")

    database = Database(url)
    await database.init()
    names = await _tables(database)
    assert "research_sessions" in names  # the new migration applied

    async with database.engine.connect() as connection:
        columns = {
            row[1]
            for row in await connection.execute(text("PRAGMA table_info(dot_runs)"))
        }
    assert "session_id" in columns
    await database.dispose()


@pytest.mark.asyncio
async def test_legacy_unversioned_database_is_adopted_then_upgraded(tmp_path) -> None:
    """A pre-migration (create_all, un-versioned) DB is adopted and upgraded."""
    import asyncio

    from sqlalchemy import text

    from app.database import migrations

    legacy = tmp_path / "legacy.db"
    url = f"sqlite+aiosqlite:///{legacy}"
    await asyncio.to_thread(migrations.upgrade_to, url, "6d095cdffa5f")
    # un-version it: exactly what a create_all deployment looked like.
    helper = Database(url)
    async with helper.engine.begin() as connection:
        await connection.execute(text("DELETE FROM alembic_version"))
    await helper.dispose()

    database = Database(url)
    await database.init()
    names = await _tables(database)
    assert names >= _ALL_TABLES
    await database.dispose()


@pytest.mark.asyncio
async def test_init_is_idempotent(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'repeat.db'}")
    await database.init()
    await database.init()
    names = await _tables(database)
    assert names >= _ALL_TABLES
    await database.dispose()


@pytest.mark.asyncio
async def test_stale_running_runs_are_failed_on_startup(repository) -> None:
    run = DotRun(topic="lost mid-flight")
    run.status = DotRunStatus.RUNNING
    await repository.save_dot_run(run)
    queued = DotRun(topic="still waiting")
    await repository.save_dot_run(queued)

    failed = await repository.fail_stale_dot_runs()
    assert failed == 1

    refreshed = await repository.get_dot_run(run.dot_run_id)
    assert refreshed is not None
    assert refreshed.status == DotRunStatus.FAILED
    assert refreshed.error == "interrupted by restart"

    untouched = await repository.get_dot_run(queued.dot_run_id)
    assert untouched is not None
    assert untouched.status == DotRunStatus.QUEUED

    assert await repository.fail_stale_dot_runs() == 0  # nothing left running
