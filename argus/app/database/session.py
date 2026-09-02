"""Async engine / session factory for Argus.

SQLite is the initial backend (portable, zero-config); the same engine call
works with a PostgreSQL URL later — just swap ``ARGUS_DATABASE_URL``
(e.g. ``postgresql+asyncpg://argus:...@localhost/argus``). Timestamps are
stored as naive UTC and re-stamped as UTC when read.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.logging import get_logger
from app.database import migrations
from app.database import models as _models  # noqa: F401 - registers models on Base.metadata
from app.database.base import Base

log = get_logger(__name__)


def _create_engine(database_url: str) -> AsyncEngine:
    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        engine = create_async_engine(database_url, **kwargs)

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ARG001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine
    return create_async_engine(database_url, **kwargs)


def _mask_url(database_url: str) -> str:
    """Strip any password from the URL before logging it."""
    try:
        parts = urlsplit(database_url)
        if parts.password:
            netloc = f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname or ""
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except ValueError:
        pass
    return database_url


class Database:
    """Owns the async engine and the session factory for Argus."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.engine: AsyncEngine = _create_engine(database_url)
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

    async def init(self) -> None:
        self._ensure_sqlite_dir()
        if self._is_in_memory():
            async with self.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        elif await self._schema_present() and not await self._has_version_table():
            # Pre-migration deployment (create_all schema, no alembic version):
            # adopt it at the base snapshot, then upgrade forward to head.
            await asyncio.to_thread(migrations.stamp_and_upgrade, self.database_url)
        else:
            await asyncio.to_thread(migrations.upgrade, self.database_url)
        log.info("database_initialized", extra={"url": _mask_url(self.database_url)})

    def _is_in_memory(self) -> bool:
        if not self.database_url.startswith("sqlite"):
            return False
        path = self.database_url.split(":///", 1)[-1]
        return path.startswith(":")

    async def _schema_present(self) -> bool:
        """True when any Argus table already exists in the database."""
        owned = set(Base.metadata.tables)
        names = await self._table_names()
        return bool(names & owned)

    async def _has_version_table(self) -> bool:
        """True when Alembic actually tracks this database (has a revision row).

        A bare ``alembic_version`` table with no rows is indistinguishable from
        an un-versioned pre-migration database — adopt that case as legacy.
        """
        names = await self._table_names()
        if "alembic_version" not in names:
            return False
        async with self.engine.connect() as connection:
            row = await connection.execute(
                text("SELECT COUNT(*) FROM alembic_version")
            )
            return (row.scalar_one() or 0) > 0

    async def _table_names(self) -> set[str]:
        async with self.engine.connect() as connection:
            return await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )

    def _ensure_sqlite_dir(self) -> None:
        if not self.database_url.startswith("sqlite"):
            return
        path = self.database_url.split(":///", 1)[-1]
        if path.startswith(":"):  # in-memory database
            return
        directory = Path(path).parent
        if str(directory) not in ("", "."):
            directory.mkdir(parents=True, exist_ok=True)

    async def dispose(self) -> None:
        await self.engine.dispose()
