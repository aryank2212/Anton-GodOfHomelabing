"""Memory usage monitor."""

from __future__ import annotations

from typing import Any

import psutil

from app.models.check import MonitorResult
from app.monitors.base import Monitor, safe_check


class MemoryMonitor(Monitor):
    """Fails when system memory usage crosses a threshold.

    ``params``:
      threshold_pct:  fail when used percent exceeds this value (default 95)
    """

    kind = "memory"

    def __init__(self, name: str, params: dict[str, Any]) -> None:
        super().__init__(name, params)
        self.threshold = float(params.get("threshold_pct", 95))

    @safe_check
    async def check(self) -> MonitorResult:
        usage = psutil.virtual_memory()
        used_pct = usage.percent
        if used_pct > self.threshold:
            return MonitorResult.failing(
                "high_usage",
                f"memory at {used_pct:.1f}% (threshold {self.threshold:g}%)",
                used_percent=round(used_pct, 1),
                threshold=self.threshold,
                available_bytes=usage.available,
            )
        return MonitorResult.healthy(
            "ok",
            used_percent=round(used_pct, 1),
            threshold=self.threshold,
            available_bytes=usage.available,
        )


def build_memory_monitor(name: str, params: dict[str, Any], clients: Any) -> Monitor:
    return MemoryMonitor(name, params)
