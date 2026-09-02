from __future__ import annotations

from fastapi import Request

from app.core.runtime import ArgusRuntime
from app.database.repository import Repository


def get_runtime(request: Request) -> ArgusRuntime:
    return request.app.state.runtime


def get_repository(request: Request) -> Repository:
    repository = request.app.state.runtime.repository
    assert repository is not None
    return repository


def paginate(total: int, limit: int, offset: int) -> dict[str, int | None]:
    next_offset = offset + limit if offset + limit < total else None
    return {"limit": limit, "offset": offset, "total": total, "next_offset": next_offset}
