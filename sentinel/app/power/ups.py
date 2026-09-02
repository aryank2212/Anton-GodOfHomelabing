"""NUT (Network UPS Tools) power monitor.

Reads power state via the ``upsc`` command-line tool. ``parse_upsc`` is a pure
function so it can be tested without a UPS present.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from typing import Any

from app.core.logging import get_logger
from app.power.base import PowerMonitor, PowerMonitorError, PowerSample, PowerStatus

log = get_logger(__name__)


def parse_upsc(output: str) -> dict[str, Any]:
    """Parse ``upsc`` key/value output into a flat dictionary."""
    values: dict[str, Any] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        try:
            values[key] = (
                float(value) if "." in value and value.replace(".", "", 1).isdigit() else value
            )
        except ValueError:  # pragma: no cover - defensive
            values[key] = value
    return values


def to_status(values: dict[str, Any]) -> PowerStatus:
    raw = str(values.get("ups.status", "")).upper()
    if "OB" in raw and "LB" in raw:
        return PowerStatus.LOW_BATTERY
    if "OB" in raw:
        return PowerStatus.ON_BATTERY
    if "OL" in raw:
        return PowerStatus.ON_LINE
    return PowerStatus.UNKNOWN


class UPSPowerMonitor(PowerMonitor):
    """Power monitor backed by NUT's ``upsc`` utility."""

    name = "ups"

    def __init__(
        self,
        command: str = "upsc",
        ups_name: str = "",
        timeout: float = 5.0,
    ) -> None:
        self._command = shutil.which(command) or command
        self._ups_name = ups_name
        self._timeout = timeout

    @property
    def available(self) -> bool:
        return shutil.which(self._command.split()[0]) is not None

    async def read(self) -> PowerSample:
        name = self._ups_name or await self._discover()
        if not name:
            raise PowerMonitorError("no UPS found (upsc -l returned nothing)")
        code, output = await self._run("", name)
        if code != 0 or not output:
            raise PowerMonitorError(f"upsc {name} failed (exit {code}): {output[:200]}")
        values = parse_upsc(output)
        status = to_status(values)
        return PowerSample(
            status=status,
            charge_percent=_as_float(values.get("battery.charge")),
            runtime_seconds=_as_float(values.get("battery.runtime")),
            input_voltage=_as_float(values.get("input.voltage")),
            battery_voltage=_as_float(values.get("battery.voltage")),
            metadata={"ups": name, "raw_status": str(values.get("ups.status", ""))},
        )

    async def _discover(self) -> str:
        code, output = await self._run("-l", "")
        if code != 0 or not output.strip():
            return ""
        return output.strip().splitlines()[0].strip()

    async def _run(self, flags: str, name: str) -> tuple[int, str]:
        if not self.available:
            raise PowerMonitorError("upsc command is not installed")
        cmd = [self._command, flags, name] if flags else [self._command, name]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
            return proc.returncode or 0, stdout.decode(errors="replace").strip()
        except TimeoutError as exc:
            raise PowerMonitorError("upsc timed out") from exc
        except OSError as exc:
            raise PowerMonitorError(f"upsc could not be run: {exc}") from exc


def _as_float(value: Any) -> float | None:
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None
