"""Async engine / session factory for Sentinel.

SQLite is the initial backend. The engine setup keeps everything UTC-aware:
SQLite has no native timezone support, so datetimes are stored as naive UTC
and re-stamped as UTC when read. The same engine call works with a PostgreSQL
URL later (just swap ``SENTINEL_DATABASE_URL``).
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.logging import get_logger
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
    """Owns the async engine and the session factory for Sentinel."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.engine: AsyncEngine = _create_engine(database_url)
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

    async def init(self) -> None:
        self._ensure_sqlite_dir()
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        log.info("database_initialized", extra={"url": _mask_url(self.database_url)})

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
