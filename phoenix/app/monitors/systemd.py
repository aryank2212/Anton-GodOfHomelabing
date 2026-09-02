"""systemd service monitor."""

from __future__ import annotations

from typing import Any

from app.core.clients import ClientUnavailableError, SystemdClient
from app.models.check import MonitorResult
from app.monitors.base import Monitor, safe_check


class SystemdMonitor(Monitor):
    """Checks that a systemd unit is in the expected state.

    ``params``:
      unit:            unit name, e.g. ``phoenix.service`` (required)
      expected_state:  ``active`` (default) or ``any``
    """

    kind = "systemd"

    def __init__(self, name: str, params: dict[str, Any], client: SystemdClient) -> None:
        super().__init__(name, params)
        self._client = client
        self.unit = str(params["unit"])
        self.expected_state = str(params.get("expected_state", "active"))

    @safe_check
    async def check(self) -> MonitorResult:
        if not self._client.available:
            raise ClientUnavailableError("systemctl is not available on this host")
        is_active = await self._client.unit_is_active(self.unit)
        if self.expected_state == "active" and not is_active:
            return MonitorResult.failing(
                "inactive", f"unit '{self.unit}' is not active", unit=self.unit
            )
        return MonitorResult.healthy("active", unit=self.unit)


def build_systemd_monitor(name: str, params: dict[str, Any], clients: Any) -> Monitor:
    if clients.systemd is None:
        raise ClientUnavailableError("no systemd client available")
    return SystemdMonitor(name, params, clients.systemd)
