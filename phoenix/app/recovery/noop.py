"""No-op recovery — record and escalate only.

Used for host-level failures (disk, memory, CPU, network) or the Docker
daemon itself where Phoenix cannot (or must not) restart the failing
component. Recovery "succeeds" trivially; the incident is still recorded and
the escalated event is still published to Hermes.
"""

from __future__ import annotations

from app.recovery.base import RecoveryResult, RecoveryStrategy


class NoopStrategy(RecoveryStrategy):
    name = "noop"

    async def execute(self) -> RecoveryResult:
        return RecoveryResult.ok(
            self.name,
            "no automated recovery configured for this component",
        )
