"""Filesystem usage monitor."""

from __future__ import annotations

from typing import Any

import psutil

from app.models.check import MonitorResult
from app.monitors.base import Monitor, safe_check


class DiskMonitor(Monitor):
    """Fails when the used space of a mount crosses a threshold.

    ``params``:
      path:           mount point, e.g. ``/`` (default ``/``)
      threshold_pct:  fail when used percent exceeds this value (default 90)
    """

    kind = "disk"

    def __init__(self, name: str, params: dict[str, Any]) -> None:
        super().__init__(name, params)
        self.path = str(params.get("path", "/"))
        self.threshold = float(params.get("threshold_pct", 90))

    @safe_check
    async def check(self) -> MonitorResult:
        usage = psutil.disk_usage(self.path)
        used_pct = usage.percent
        if used_pct > self.threshold:
            return MonitorResult.failing(
                "full",
                f"{self.path} is {used_pct:.1f}% full (threshold {self.threshold:g}%)",
                path=self.path,
                used_percent=round(used_pct, 1),
                threshold=self.threshold,
                free_bytes=usage.free,
            )
        return MonitorResult.healthy(
            "ok",
            path=self.path,
            used_percent=round(used_pct, 1),
            threshold=self.threshold,
            free_bytes=usage.free,
            total_bytes=usage.total,
        )


def build_disk_monitor(name: str, params: dict[str, Any], clients: Any) -> Monitor:
    return DiskMonitor(name, params)
