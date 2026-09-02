"""UPS observer — power state via NUT's ``upsc``.

Wraps :class:`app.power.ups.UPSPowerMonitor` and turns power samples into
standardized observations. Sentinel only observes; it never initiates a
shutdown (that is Phoenix).
"""

from __future__ import annotations

from collections.abc import Sequence

from app.config.loader import ObserverSpec
from app.config.settings import Settings
from app.core.clients import Clients
from app.core.logging import get_logger
from app.models.observation import Category, Observation, Severity
from app.observers.base import Observer
from app.power.base import PowerMonitorError, PowerStatus
from app.power.ups import UPSPowerMonitor

log = get_logger(__name__)

_STATUS_SEVERITY = {
    PowerStatus.ON_LINE: Severity.INFO,
    PowerStatus.ON_BATTERY: Severity.MEDIUM,
    PowerStatus.LOW_BATTERY: Severity.CRITICAL,
    PowerStatus.UNKNOWN: Severity.INFO,
}


class UPSObserver(Observer):
    name = "ups"
    category = Category.POWER
    description = "UPS / power state via NUT"

    def __init__(self, spec: ObserverSpec, settings: Settings, clients: Clients) -> None:
        super().__init__(spec, default_interval=30.0, default_timeout=10.0)
        params = spec.params
        self.monitor = UPSPowerMonitor(
            command=str(params.get("command", "upsc")),
            ups_name=str(params.get("ups_name", "")),
            timeout=float(params.get("timeout", 5)),
        )

    async def collect(self) -> Sequence[Observation]:
        if not self.monitor.available:
            log.debug("ups_not_installed")
            return []

        try:
            sample = await self.monitor.read()
        except PowerMonitorError as exc:
            log.warning("ups_read_failed", extra={"error": str(exc)})
            return [
                self._observation(
                    object="ups",
                    state="unavailable",
                    severity=Severity.MEDIUM,
                    confidence=0.7,
                    metadata={"error": str(exc)},
                    tags=["power", "ups"],
                )
            ]

        return [
            self._observation(
                object="ups",
                state=sample.status.value,
                severity=_STATUS_SEVERITY[sample.status],
                confidence=0.95,
                metadata={
                    "charge_percent": sample.charge_percent,
                    "runtime_seconds": sample.runtime_seconds,
                    "input_voltage": sample.input_voltage,
                    "battery_voltage": sample.battery_voltage,
                    **sample.metadata,
                },
                tags=["power", "ups"],
            )
        ]


def build_ups_observer(spec: ObserverSpec, settings: Settings, clients: Clients) -> Observer:
    return UPSObserver(spec, settings, clients)
