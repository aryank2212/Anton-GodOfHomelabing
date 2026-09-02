from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

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
    """Owns the async engine and the session factory for Phoenix."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.engine: AsyncEngine = _create_engine(database_url)
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

    async def init(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        log.info(
            "database_initialized",
            extra={"url": _mask_url(self.database_url)},
        )

    async def dispose(self) -> None:
        await self.engine.dispose()
