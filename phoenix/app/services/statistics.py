"""Recovery statistics — the "Learn" step of the Phoenix loop.

Aggregates over the persistent incident history:

* how many failures (and by component / by day),
* mean recovery time,
* recovery success rate,
* most unstable services,
* recovery frequency by strategy and component.

These numbers later feed AI analysis on Oracle; Phoenix only aggregates.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.database.repository import Repository

log = get_logger(__name__)


class StatisticsService:
    def __init__(self, repository: Repository) -> None:
        self._repo = repository

    async def overview(self) -> dict[str, object]:
        total = await self._repo.count_incidents()
        resolved = await self._repo.count_incidents(status="resolved")
        unresolved = await self._repo.count_incidents(status="unresolved")
        mean_recovery = await self._repo.mean_recovery_time()

        closed = resolved + unresolved
        success_rate = (resolved / closed) if closed else None

        return {
            "period": "all",
            "total_incidents": total,
            "by_status": {
                "open": await self._repo.count_incidents(status="open"),
                "recovering": await self._repo.count_incidents(status="recovering"),
                "resolved": resolved,
                "unresolved": unresolved,
                "maintenance": await self._repo.count_incidents(status="maintenance"),
            },
            "mean_recovery_time_seconds": round(mean_recovery, 2) if mean_recovery else None,
            "recovery_success_rate": round(success_rate, 4) if success_rate is not None else None,
            "most_unstable_services": await self._most_unstable(limit=10),
            "failure_frequency": {
                "by_component": [
                    {"component": c, "incidents": n}
                    for c, n in await self._repo.incidents_by_component(limit=50)
                ],
                "by_day": [
                    {"day": d, "incidents": n} for d, n in await self._repo.incidents_by_day()
                ],
            },
            "recovery_frequency": {
                "by_strategy": [
                    {"strategy": s or "none", "recoveries": n}
                    for s, n in await self._repo.recoveries_by_strategy()
                ],
                "by_component": [
                    {"component": c, "recoveries": n}
                    for c, n in await self._repo.recoveries_by_component()
                ],
            },
        }

    async def _most_unstable(self, limit: int) -> list[dict[str, object]]:
        by_component = dict(await self._repo.incidents_by_component(limit=100))
        unresolved = dict(await self._repo.unresolved_by_component(limit=100))
        services = [
            {
                "component": component,
                "incidents": by_component[component],
                "unresolved": unresolved.get(component, 0),
            }
            for component in sorted(
                by_component,
                key=lambda c: (unresolved.get(c, 0), by_component[c]),
                reverse=True,
            )
        ]
        return services[:limit]
