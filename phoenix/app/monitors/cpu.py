"""CPU usage monitor."""

from __future__ import annotations

from typing import Any

import psutil

from app.models.check import MonitorResult
from app.monitors.base import Monitor, safe_check


class CpuMonitor(Monitor):
    """Fails when sustained CPU usage crosses a threshold.

    ``params``:
      threshold_pct:  fail when CPU percent exceeds this value (default 95)
      interval:       sampling window in seconds (default 1.0)
    """

    kind = "cpu"

    def __init__(self, name: str, params: dict[str, Any]) -> None:
        super().__init__(name, params)
        self.threshold = float(params.get("threshold_pct", 95))
        self.interval = float(params.get("interval", 1.0))

    @safe_check
    async def check(self) -> MonitorResult:
        used_pct = psutil.cpu_percent(interval=self.interval)
        if used_pct > self.threshold:
            return MonitorResult.failing(
                "high_usage",
                f"cpu at {used_pct:.1f}% (threshold {self.threshold:g}%)",
                used_percent=round(used_pct, 1),
                threshold=self.threshold,
            )
        return MonitorResult.healthy(
            "ok", used_percent=round(used_pct, 1), threshold=self.threshold
        )


def build_cpu_monitor(name: str, params: dict[str, Any], clients: Any) -> Monitor:
    return CpuMonitor(name, params)
