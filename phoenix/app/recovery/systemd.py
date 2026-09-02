"""Restart a systemd service."""

from __future__ import annotations

from typing import Any

from app.core.clients import ClientUnavailableError, SystemdClient
from app.recovery.base import RecoveryError, RecoveryResult, RecoveryStrategy


class SystemdRestartStrategy(RecoveryStrategy):
    """Restart a unit through ``systemctl``.

    ``params``:
      unit:  unit name, e.g. ``phoenix.service`` (required)
    """

    name = "systemd_restart"

    def __init__(self, params: dict[str, Any], clients: Any) -> None:
        super().__init__(params, clients)
        self.unit = str(params["unit"])

    async def execute(self) -> RecoveryResult:
        client: SystemdClient = self.clients.systemd
        if client is None or not client.available:
            raise RecoveryError("no systemd client available")
        try:
            await client.restart_unit(self.unit)
        except ClientUnavailableError as exc:
            raise RecoveryError(f"failed to restart '{self.unit}': {exc}") from exc
        return RecoveryResult.ok(
            self.name,
            f"unit '{self.unit}' restarted",
            unit=self.unit,
        )
