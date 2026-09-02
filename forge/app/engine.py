"""Execution engine — the heart of Forge.

Turns a tool call into a policy decision and an execution (or an approval
request), records everything in the audit log, enforces cooldowns, rate limits
and crash-loop escalation, and notifies Hermes when human approval is needed
or an action ran.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.approval import ApprovalStore
from app.audit import AuditLog
from app.config import Settings
from app.policy import Policy
from app.schemas import Decision, ToolCall, ToolCallResponse
from app.tools.registry import ToolRegistry

log = logging.getLogger("forge.engine")

#: Act tools that keep a container running; used for crash-loop escalation.
_RESTART_FAMILY = {"docker_restart", "docker_start", "phoenix_recover"}


class HermesClient:
    """Minimal client for the Hermes approval bridge + event feed."""

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "anton-forge/1.0"})

    async def request_approval(self, approval_id: str, text: str) -> bool:
        try:
            response = await self._client.post(
                f"{self._base}/v1/approvals",
                json={"id": approval_id, "text": text},
            )
            return response.status_code < 300
        except httpx.HTTPError as exc:
            log.warning("forge_hermes_approval_unreachable", extra={"error": str(exc)})
            return False

    async def publish_event(
        self,
        *,
        type_: str,
        severity: str,
        title: str,
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        try:
            await self._client.post(
                f"{self._base}/event",
                json={
                    "module": "forge",
                    "type": type_,
                    "severity": severity,
                    "title": title,
                    "message": message,
                    "metadata": metadata or {},
                    "tags": ["forge", "ai"],
                },
            )
        except httpx.HTTPError as exc:
            log.warning("forge_hermes_event_unreachable", extra={"error": str(exc)})

    async def close(self) -> None:
        await self._client.aclose()


class ExecutionEngine:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: Policy,
        approvals: ApprovalStore,
        audit: AuditLog,
        settings: Settings,
        hermes: HermesClient,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._approvals = approvals
        self._audit = audit
        self._settings = settings
        self._hermes = hermes
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._hermes.close()

    async def call(self, request: ToolCall, caller: str = "unknown") -> ToolCallResponse:
        tool = self._registry.get(request.tool)
        if tool is None:
            return ToolCallResponse(
                ok=False,
                tool=request.tool,
                error=f"unknown tool '{request.tool}'",
                output=f"unknown tool '{request.tool}'",
            )
        target = tool.identity(request.args)
        decision = self._policy.decide(tool.spec.name, tool.spec.risk, tool.spec.read_only, target)

        if decision.decision == "blocked":
            await self._audit.record(
                {
                    "tool": tool.spec.name,
                    "target": target,
                    "args": request.args,
                    "caller": caller,
                    "decision": decision.decision,
                    "error": decision.message,
                    "reason": request.reason,
                }
            )
            return ToolCallResponse(
                ok=False,
                tool=tool.spec.name,
                error=decision.message,
                output=decision.message,
                decision="blocked",
            )

        if decision.decision == "allowed":
            return await self._execute(tool.spec.name, request, caller, decision.decision, target)

        if decision.decision == "auto":
            gate = await self._auto_gate(tool.spec.name, target)
            if gate is not None:
                await self._audit.record(
                    {
                        "tool": tool.spec.name,
                        "target": target,
                        "args": request.args,
                        "caller": caller,
                        "decision": "approval",
                        "reason": gate,
                    }
                )
                return await self._start_approval(tool.spec.name, request, caller, target, gate)
            return await self._execute(tool.spec.name, request, caller, "auto", target)

        # decision.decision == "approval"
        return await self._start_approval(tool.spec.name, request, caller, target, decision.message)

    # -- execution ----------------------------------------------------------

    async def _execute(
        self,
        tool_name: str,
        request: ToolCall,
        caller: str,
        decision: Decision,
        target: str,
    ) -> ToolCallResponse:
        tool = self._registry.get(tool_name)
        assert tool is not None
        try:
            result = await tool.run(request.args)
        except Exception as exc:  # noqa: BLE001 - a failing tool must not kill the loop
            log.exception("forge_tool_error", extra={"tool": tool_name, "target": target})
            error = f"{type(exc).__name__}: {exc}"
            await self._audit.record(
                {
                    "tool": tool_name,
                    "target": target,
                    "args": request.args,
                    "caller": caller,
                    "decision": decision,
                    "ok": False,
                    "error": error,
                }
            )
            await self._hermes.publish_event(
                type_="forge.action.failed",
                severity="warning",
                title=f"Forge {tool_name} {target} failed",
                message=error[:500],
                metadata={"tool": tool_name, "target": target, "caller": caller},
            )
            return ToolCallResponse(
                ok=False, tool=tool_name, error=error, output=error, decision=decision
            )

        await self._audit.record(
            {
                "tool": tool_name,
                "target": target,
                "args": request.args,
                "caller": caller,
                "decision": decision,
                "ok": result.ok,
                "error": result.error,
                "output": (result.output or "")[: self._settings.max_output_chars],
            }
        )
        if tool.spec.read_only:
            return ToolCallResponse(
                ok=result.ok,
                tool=tool_name,
                output=result.output[: self._settings.max_output_chars],
                data=result.data,
                error=result.error,
                decision="allowed",
            )

        await self._hermes.publish_event(
            type_="forge.action.ok" if result.ok else "forge.action.failed",
            severity="info" if result.ok else "warning",
            title=f"Forge {tool_name} {target} {'ok' if result.ok else 'failed'}",
            message=result.error or result.output[:500],
            metadata={"tool": tool_name, "target": target, "caller": caller, "ok": result.ok},
        )
        return ToolCallResponse(
            ok=result.ok,
            tool=tool_name,
            output=result.output[: self._settings.max_output_chars],
            data=result.data,
            error=result.error,
            decision=decision,
        )

    # -- approvals ----------------------------------------------------------

    async def _start_approval(
        self,
        tool_name: str,
        request: ToolCall,
        caller: str,
        target: str,
        gate_reason: str,
    ) -> ToolCallResponse:
        if await self._approvals.count_for(tool_name, target) > 0:
            message = f"{tool_name} {target}: an approval is already pending"
            await self._audit.record(
                {
                    "tool": tool_name,
                    "target": target,
                    "args": request.args,
                    "caller": caller,
                    "decision": "approval",
                    "error": message,
                }
            )
            return ToolCallResponse(
                ok=False, tool=tool_name, error=message, output=message, decision="approval"
            )

        tool = self._registry.get(tool_name)
        assert tool is not None
        approval = await self._approvals.create(
            tool=tool_name,
            target=target,
            command=self._render_command(tool_name, request.args),
            risk=tool.spec.risk,
            reason=request.reason or gate_reason,
            caller=caller,
            args=request.args,
        )
        message = self._render_approval_text(approval.to_out().model_dump(), tool.spec.risk)
        sent = await self._hermes.request_approval(approval.id, message)
        await self._audit.record(
            {
                "tool": tool_name,
                "target": target,
                "args": request.args,
                "caller": caller,
                "decision": "approval",
                "approval_id": approval.id,
                "reason": request.reason or gate_reason,
                "hermes_notified": sent,
            }
        )
        if not sent:
            await self._approvals.expire(approval.id)
            error = "cannot reach Hermes for approval; action not run"
            return ToolCallResponse(
                ok=False, tool=tool_name, error=error, output=error, decision="approval"
            )
        output = (
            f"⏳ {tool_name} {target} requires approval (id {approval.id}); "
            "not run yet. Operator thumbs-up will execute it."
        )
        if approval.reason:
            output = (
                f"⏳ {tool_name} {target} requires approval (id {approval.id}); "
                f"{approval.reason}. Not run yet — operator thumbs-up will execute it."
            )
        return ToolCallResponse(
            ok=False,
            tool=tool_name,
            output=output,
            error=output,
            decision="approval",
            approval_id=approval.id,
        )

    async def resolve(
        self, approval_id: str, approved: bool, by: str = "telegram"
    ) -> ToolCallResponse | None:
        approval = await self._approvals.get(approval_id)
        if approval is None:
            return None
        resolved = await self._approvals.resolve(approval_id, approved, by)
        if resolved is None:
            return None
        if not approved:
            await self._audit.record(
                {
                    "tool": approval.tool,
                    "target": approval.target,
                    "args": approval.args,
                    "caller": approval.caller,
                    "decision": "approval",
                    "approval_id": approval_id,
                    "ok": False,
                    "error": "rejected by operator",
                }
            )
            await self._hermes.publish_event(
                type_="forge.action.rejected",
                severity="info",
                title=f"Forge {approval.tool} {approval.target} rejected",
                message=f"rejected by {by}",
                metadata={
                    "approval_id": approval_id,
                    "tool": approval.tool,
                    "target": approval.target,
                },
            )
            return None
        request = ToolCall(tool=approval.tool, args=approval.args, reason=approval.reason)
        return await self._execute(
            approval.tool,
            request,
            f"{approval.caller}/approved-by-{by}",
            "approval",
            approval.target,
        )

    async def sweep(self) -> None:
        for approval in await self._approvals.sweep():
            await self._audit.record(
                {
                    "tool": approval.tool,
                    "target": approval.target,
                    "args": approval.args,
                    "caller": approval.caller,
                    "decision": "approval",
                    "approval_id": approval.id,
                    "ok": False,
                    "error": "expired without operator response",
                }
            )
            await self._hermes.publish_event(
                type_="forge.action.expired",
                severity="info",
                title=f"Forge {approval.tool} {approval.target} expired",
                message="no operator response before the approval window closed",
                metadata={
                    "approval_id": approval.id,
                    "tool": approval.tool,
                    "target": approval.target,
                },
            )

    # -- gating -------------------------------------------------------------

    async def _auto_gate(self, tool_name: str, target: str) -> str | None:
        """Return a human reason to escalate an auto action to approval, or
        ``None`` when the action may auto-run."""
        settings = self._policy.config.cooldowns
        last = await self._audit.last_executed(tool=tool_name, target=target)
        if (
            last is not None
            and (datetime.now(UTC) - last).total_seconds() < settings.target_seconds
        ):
            return f"target recently acted on; cooldown {settings.target_seconds:.0f}s"

        if tool_name in _RESTART_FAMILY:
            window = settings.crashloop_window_seconds
            restarts = await self._audit.count_recent(tool=tool_name, target=target, seconds=window)
            if restarts >= settings.crashloop_threshold:
                return (
                    f"{target} has been {tool_name}-ed {restarts} times in the last "
                    f"{window / 60:.0f} min (crash-looping); operator decision required"
                )

        # rate limit from preapproval entry
        entry = next(
            (
                candidate
                for candidate in self._policy.config.preapproved
                if candidate.tool == tool_name
            ),
            None,
        )
        if entry is not None:
            used = await self._audit.count_recent(tool=tool_name, target=target, seconds=3600)
            if used >= entry.max_per_hour:
                return f"rate limit reached ({entry.max_per_hour}/h) for {tool_name} {target}"
        return None

    # -- rendering ----------------------------------------------------------

    def _render_command(self, tool_name: str, args: dict[str, Any]) -> str:
        try:
            return f"{tool_name} {json.dumps(args, default=str)}"
        except (TypeError, ValueError):
            return f"{tool_name} {args}"

    def _render_approval_text(self, approval: dict[str, Any], risk: str) -> str:
        return (
            "🔐 Forge needs your approval (Level 1)\n\n"
            f"<b>Action:</b> <code>{approval['command']}</code>\n"
            f"<b>Target:</b> {approval['target']}\n"
            f"<b>Risk:</b> {risk}\n"
            f"<b>Why:</b> {approval.get('reason') or '—'}\n\n"
            f'Reply <b>"yes"</b> to approve, <b>"no"</b> to reject.\n'
            f"Expires in {self._settings.approval_timeout / 60:.0f} min."
        )
