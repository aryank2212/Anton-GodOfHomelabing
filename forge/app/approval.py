"""Pending Level-1 approvals.

An approval is created whenever the policy says an act tool needs a human
thumbs-up. Hermes renders it as a Telegram message; the operator's reply
resolves it through ``POST /v1/approvals/{id}/resolve``. A background sweeper
expires stale approvals.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from app.schemas import ApprovalOut, ApprovalState


class Approval:
    __slots__ = (
        "id",
        "tool",
        "target",
        "command",
        "risk",
        "reason",
        "caller",
        "args",
        "created_at",
        "expires_at",
        "state",
        "resolved_at",
        "resolved_by",
    )

    def __init__(
        self,
        *,
        tool: str,
        target: str,
        command: str,
        risk: str,
        reason: str,
        caller: str,
        args: dict[str, Any],
        timeout: float,
    ) -> None:
        self.id = secrets.token_hex(6)
        self.tool = tool
        self.target = target
        self.command = command
        self.risk = risk
        self.reason = reason
        self.caller = caller
        self.args = args
        self.created_at = datetime.now(UTC)
        self.expires_at = self.created_at + timedelta(seconds=timeout)
        self.state: ApprovalState = "pending"
        self.resolved_at: datetime | None = None
        self.resolved_by: str | None = None

    @property
    def expired(self) -> bool:
        return datetime.now(UTC) >= self.expires_at

    def to_out(self) -> ApprovalOut:
        return ApprovalOut(
            id=self.id,
            tool=self.tool,
            target=self.target,
            command=self.command,
            risk=self.risk,
            reason=self.reason,
            caller=self.caller,
            created_at=self.created_at,
            expires_at=self.expires_at,
            state=self.state,
        )


class ApprovalStore:
    def __init__(self, timeout: float) -> None:
        self._timeout = timeout
        self._lock = asyncio.Lock()
        self._approvals: dict[str, Approval] = {}

    async def create(
        self,
        *,
        tool: str,
        target: str,
        command: str,
        risk: str,
        reason: str,
        caller: str,
        args: dict[str, Any],
    ) -> Approval:
        approval = Approval(
            tool=tool,
            target=target,
            command=command,
            risk=risk,
            reason=reason,
            caller=caller,
            args=args,
            timeout=self._timeout,
        )
        async with self._lock:
            self._approvals[approval.id] = approval
        return approval

    async def get(self, approval_id: str) -> Approval | None:
        async with self._lock:
            return self._approvals.get(approval_id)

    async def resolve(
        self, approval_id: str, approved: bool, by: str = "telegram"
    ) -> Approval | None:
        async with self._lock:
            approval = self._approvals.get(approval_id)
            if approval is None or approval.state != "pending":
                return None
            approval.state = "approved" if approved else "rejected"
            approval.resolved_at = datetime.now(UTC)
            approval.resolved_by = by
            return approval

    async def expire(self, approval_id: str) -> Approval | None:
        async with self._lock:
            approval = self._approvals.get(approval_id)
            if approval is None or approval.state != "pending":
                return None
            approval.state = "expired"
            approval.resolved_at = datetime.now(UTC)
            approval.resolved_by = "sweeper"
            return approval

    async def sweep(self) -> list[Approval]:
        expired: list[Approval] = []
        async with self._lock:
            for approval in list(self._approvals.values()):
                if approval.state == "pending" and approval.expired:
                    approval.state = "expired"
                    approval.resolved_at = datetime.now(UTC)
                    approval.resolved_by = "sweeper"
                    expired.append(approval)
        return expired

    async def pending(self) -> list[Approval]:
        async with self._lock:
            return [a for a in self._approvals.values() if a.state == "pending"]

    async def count_for(self, tool: str, target: str) -> int:
        """Pending approvals for the same tool+target (one at a time)."""
        async with self._lock:
            return sum(
                1
                for a in self._approvals.values()
                if a.state == "pending" and a.tool == tool and a.target == target
            )
