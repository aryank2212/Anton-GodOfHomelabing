"""In-memory snapshot of the most recent monitor results.

Used by ``GET /health`` so the API can report the current state of every
monitor without querying the database. Thread/loop safe enough for the single
asyncio loop Phoenix runs on.
"""

from __future__ import annotations

from app.models.check import MonitorResult


class HealthSnapshot:
    def __init__(self) -> None:
        self._results: dict[str, MonitorResult] = {}

    def record(self, monitor_name: str, result: MonitorResult) -> None:
        self._results[monitor_name] = result

    def result(self, monitor_name: str) -> MonitorResult | None:
        return self._results.get(monitor_name)

    def all(self) -> dict[str, MonitorResult]:
        return dict(self._results)

    def unhealthy_count(self) -> int:
        return sum(1 for result in self._results.values() if not result.ok)
