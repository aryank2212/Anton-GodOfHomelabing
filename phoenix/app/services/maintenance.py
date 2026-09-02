"""Maintenance log — planned work on components.

While a component is inside an active maintenance window, failures on its
monitors are recorded as ``maintenance`` incidents and automated recovery is
skipped, so Phoenix never fights an operator working on the box.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.database.repository import Repository


class MaintenanceCreate(BaseModel):
    component: str = Field(min_length=1, max_length=128)
    start_time: datetime | None = None
    end_time: datetime | None = None
    reason: str = Field(default="", max_length=2048)


class MaintenanceService:
    def __init__(self, repository: Repository) -> None:
        self._repo = repository

    async def is_in_maintenance(self, component: str) -> bool:
        return await self._repo.active_maintenance_for(component)

    async def create(self, payload: MaintenanceCreate) -> dict[str, object]:
        start = payload.start_time or datetime.now(UTC)
        return await self._repo.create_maintenance(
            component=payload.component,
            start_time=start,
            end_time=payload.end_time,
            reason=payload.reason,
        )

    async def list(self, limit: int, offset: int) -> list[dict[str, object]]:
        return await self._repo.list_maintenance(limit, offset)

    async def close(self, maintenance_id: int) -> bool:
        return await self._repo.close_maintenance(maintenance_id)
