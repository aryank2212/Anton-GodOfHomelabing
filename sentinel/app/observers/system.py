"""System observer — CPU, memory, disk and load on the Anton host.

Uses ``psutil`` (bundled). Thresholds come from ``observers.yaml`` params.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from app.config.loader import ObserverSpec
from app.config.settings import Settings
from app.core.clients import Clients
from app.core.logging import get_logger
from app.models.observation import Category, Observation, Severity
from app.observers.base import Observer

log = get_logger(__name__)


class SystemObserver(Observer):
    name = "system"
    category = Category.SYSTEM
    description = "CPU, memory, disk and load on the Anton host"

    def __init__(self, spec: ObserverSpec, settings: Settings, clients: Clients) -> None:
        super().__init__(spec, default_interval=30.0, default_timeout=10.0)
        params = spec.params
        self.cpu_high = float(params.get("cpu_high", 80.0))
        self.memory_high = float(params.get("memory_high", 80.0))
        self.disk_warn = float(params.get("disk_warn", 80.0))
        self.disk_critical = float(params.get("disk_critical", 90.0))

    async def collect(self) -> Sequence[Observation]:
        return await asyncio.to_thread(self._collect_sync)

    def _collect_sync(self) -> list[Observation]:
        try:
            import psutil
        except ImportError:  # pragma: no cover - psutil is a dependency
            log.warning("system_psutil_missing")
            return []

        observations: list[Observation] = []

        cpu = psutil.cpu_percent(interval=0.2)
        observations.append(
            self._observation(
                object="cpu",
                state="high" if cpu >= self.cpu_high else "ok",
                severity=Severity.MEDIUM if cpu >= self.cpu_high else Severity.INFO,
                confidence=0.9,
                metadata={"percent": round(cpu, 1)},
                tags=["system", "cpu"],
            )
        )

        memory = psutil.virtual_memory().percent
        observations.append(
            self._observation(
                object="memory",
                state="high" if memory >= self.memory_high else "ok",
                severity=Severity.MEDIUM if memory >= self.memory_high else Severity.INFO,
                confidence=0.9,
                metadata={"percent": round(memory, 1)},
                tags=["system", "memory"],
            )
        )

        load1, load5, load15 = psutil.getloadavg()
        observations.append(
            self._observation(
                object="load",
                state="ok",
                severity=Severity.INFO,
                confidence=0.9,
                metadata={"load1": load1, "load5": load5, "load15": load15},
                tags=["system", "load"],
            )
        )

        for part in psutil.disk_partitions(all=False):
            if not part.fstype or not part.device.startswith("/dev"):
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            percent = usage.percent
            if percent >= self.disk_critical:
                state, severity = "critical", Severity.CRITICAL
            elif percent >= self.disk_warn:
                state, severity = "warn", Severity.MEDIUM
            else:
                state, severity = "ok", Severity.INFO
            observations.append(
                self._observation(
                    object=f"disk:{part.mountpoint}",
                    state=state,
                    severity=severity,
                    confidence=0.9,
                    metadata={
                        "mount": part.mountpoint,
                        "device": part.device,
                        "percent": round(percent, 1),
                        "total_bytes": usage.total,
                    },
                    tags=["system", "disk"],
                )
            )

        return observations


def build_system_observer(spec: ObserverSpec, settings: Settings, clients: Clients) -> Observer:
    return SystemObserver(spec, settings, clients)
