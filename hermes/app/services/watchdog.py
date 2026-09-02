from __future__ import annotations

import asyncio
import fnmatch
import json
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.config.settings import Settings
from app.core.logging import get_logger
from app.core.queue import NotificationQueue
from app.database.models import EventRecord
from app.database.session import Database
from app.providers.registry import ProviderRegistry
from app.services.context import build_ai_context
from app.services.oracle import OracleClient

if TYPE_CHECKING:
    from app.bot.telegram_bot import TelegramBot

log = get_logger(__name__)

#: Async command runner: (command, timeout) -> (ok, output). Injectable for tests.
ShellRunner = Any

#: Container existence probe: (name) -> exists. Injectable for tests.
ContainerExists = Callable[[str], Awaitable[bool]]

#: Supported recovery actions the watchdog may propose. Every rendered command
#: must ALSO match HERMES_WATCHDOG_ALLOWED_COMMANDS (fail-closed).
_ACTIONS = {
    "docker_restart": "docker restart {target}",
    "docker_start": "docker start {target}",
    "docker_stop": "docker stop {target}",
    "docker_logs": "docker logs --tail 40 {target}",
}

#: A single, simple container identifier. Never anything that could break out
#: of the intended command shape.
_TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

_APPROVE_WORDS = {
    "yes",
    "y",
    "yeah",
    "yep",
    "ok",
    "okay",
    "sure",
    "go",
    "go ahead",
    "do it",
    "approve",
    "approved",
    "confirm",
    "proceed",
    "run it",
}
_REJECT_WORDS = {
    "no",
    "n",
    "nope",
    "cancel",
    "stop",
    "dont",
    "don't",
    "abort",
    "skip",
    "veto",
    "reject",
    "stand down",
    "hold off",
    "never mind",
}

#: Triggering severities for the watch loop.
_TRIGGER_SEVERITIES = ("error", "critical")

#: Actions that are considered safe enough to auto-run after the veto window.
#: Everything else (notably ``docker_stop``, which keeps a container down
#: until someone starts it again) requires explicit operator approval.
_AUTO_SAFE_ACTIONS = frozenset({"docker_restart", "docker_start", "docker_logs"})


def local_risk(action: str) -> str:
    """Deterministic per-action risk used to gate auto-run.

    The model's own ``risk`` field is advisory only (kept as ``risk_model``):
    whether a fix may auto-run must not depend on the model's mood, so the
    effective risk comes from this local policy.
    """
    return "low" if action in _AUTO_SAFE_ACTIONS else "medium"


#: Phoenix recovery strategy names equivalent to each watchdog action.
_ACTION_TO_PHOENIX_STRATEGY = {
    "docker_restart": "docker_restart",
    "docker_start": "docker_start",
    "docker_stop": "docker_stop",
    "docker_logs": None,
}


def phoenix_prior_attempts(event: EventRecord, action: str) -> tuple[str | None, int]:
    """Return ``(component, attempts)`` when ``event`` is a Phoenix escalation
    that already exhausted ``action``'s recovery strategy.

    Phoenix recovers components itself and only escalates to Hermes after its
    own retries failed. Auto-running the same strategy again would repeat a
    proven-failing attempt and risk fighting Phoenix's dependency cascade, so
    such proposals must require operator approval instead of auto-running.
    """
    if event.module != "phoenix" or event.type != "recovery_escalated":
        return None, 0
    strategy = _ACTION_TO_PHOENIX_STRATEGY.get(action)
    if strategy is None:
        return None, 0
    metadata = event.metadata_json or {}
    if metadata.get("strategy") != strategy:
        return None, 0
    try:
        attempts = int(metadata.get("attempts") or 0)
    except (TypeError, ValueError):
        attempts = 0
    if attempts < 1:
        return None, 0
    return metadata.get("component") or None, attempts


@dataclass(frozen=True)
class WatchdogDecision:
    """A structured recovery proposal parsed from the model's reply."""

    action: str
    target: str
    risk: str
    reason: str = ""


@dataclass
class PendingAction:
    """A proposal awaiting operator response or the auto-run window."""

    chat_id: int
    event_id: str
    target: str
    action: str
    command: str
    risk: str
    reason: str = ""
    risk_model: str = ""
    task: asyncio.Task[None] | None = None
    replied: bool = False
    created: datetime = field(default_factory=lambda: datetime.now(UTC))


def parse_decision(raw: str) -> WatchdogDecision | None:
    """Extract the strict JSON decision from the model's reply.

    Tolerates markdown code fences and surrounding prose; the first balanced
    ``{...}`` object wins. Returns ``None`` when the reply does not contain a
    usable decision (the watchdog then simply skips the event).
    """
    if not raw:
        return None
    text = re.sub(r"```(?:json)?\s*", "", raw).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    action = str(data.get("action") or "none").strip()
    risk = str(data.get("risk") or "low").strip().lower()
    target = str(data.get("target") or "").strip()
    reason = str(data.get("reason") or "").strip()
    if action not in _ACTIONS:
        action = "none"
    if risk not in ("low", "medium", "high"):
        risk = "medium"
    return WatchdogDecision(action=action, target=target, risk=risk, reason=reason)


def action_to_command(action: str, target: str) -> str | None:
    template = _ACTIONS.get(action)
    if template is None:
        return None
    return template.format(target=target)


class Watchdog:
    """The AI monitor-and-manager loop for Anton.

    Watches the event stream for failures, asks the Oracle gateway whether an
    allow-listed recovery action is warranted, and handles the proposal
    lifecycle:

    * ``low`` risk proposals announce the fix and auto-run after a veto window
      (``HERMES_WATCHDOG_CONFIRM_SECONDS``) unless the operator replies.
    * ``medium``/``high`` proposals require explicit operator approval.
    * Any operator reply to an open proposal is honoured immediately.

    Safety is enforced locally and never delegated to the model: proposals are
    limited to a fixed action set, targets must be simple identifiers, and the
    rendered command must match ``HERMES_WATCHDOG_ALLOWED_COMMANDS``. With an
    empty allow-list the watchdog refuses to run anything.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        database: Database,
        queue: NotificationQueue,
        registry: ProviderRegistry,
        oracle: OracleClient,
        bot: TelegramBot | None = None,
        check_interval: float | None = None,
        run_command: ShellRunner | None = None,
        container_exists: ContainerExists | None = None,
    ) -> None:
        self._settings = settings
        self._database = database
        self._queue = queue
        self._registry = registry
        self._oracle = oracle
        self._bot = bot
        self._interval = check_interval or settings.watchdog_check_interval
        self._run_command = run_command
        #: Container existence probe, overridable so tests never touch Docker.
        self._container_exists = container_exists or self._default_container_exists
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._last_check: datetime | None = None
        self._evaluated: set[str] = set()
        self._pending: dict[str, PendingAction] = {}
        self._last_target: dict[str, datetime] = {}
        #: Executed-action timestamps per target, used to detect crash loops.
        self._action_history: dict[str, list[datetime]] = {}

    @property
    def enabled(self) -> bool:
        return bool(
            self._settings.watchdog_enabled
            and self._settings.ai_enabled
            and self._settings.oracle_url
            and self._settings.watchdog_command_patterns
            and self._bot is not None
            and self._bot.enabled
        )

    @property
    def pending(self) -> list[PendingAction]:
        return sorted(self._pending.values(), key=lambda p: p.created, reverse=True)

    @property
    def primary_chat_id(self) -> int | None:
        if self._bot is None:
            return None
        chats = self._bot.allowed_chats
        return chats[0] if chats else None

    async def start(self) -> None:
        if not self.enabled:
            log.info("watchdog_disabled")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="hermes-watchdog")
        log.info(
            "watchdog_started",
            extra={
                "check_interval": self._interval,
                "confirm_seconds": self._settings.watchdog_confirm_seconds,
                "allowed_commands": self._settings.watchdog_command_patterns,
            },
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        for pending in list(self._pending.values()):
            if pending.task is not None:
                pending.task.cancel()
        self._pending.clear()

    # -- Watch loop -----------------------------------------------------------

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.check_once()
            except Exception:
                log.exception("watchdog_check_failed")
            await asyncio.sleep(self._interval)

    async def check_once(self, now: datetime | None = None) -> list[str]:
        """Evaluate newly arrived failure events; returns processed event ids."""
        if not self.enabled:
            return []
        now = now or datetime.now(UTC)
        if self._last_check is None:
            # On first run skip the historical backlog: only react to events
            # that arrive from now on.
            self._last_check = now
            return []
        events = await self._fetch_candidates(now)
        self._last_check = now
        processed: list[str] = []
        for event in events:
            if event.id in self._evaluated:
                continue
            self._evaluated.add(event.id)
            processed.append(event.id)
            await self._evaluate(event)
        return processed

    async def _fetch_candidates(self, now: datetime) -> list[EventRecord]:
        since = self._last_check or now
        async with self._database.session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(EventRecord)
                        .where(
                            EventRecord.timestamp > since,
                            EventRecord.timestamp <= now,
                            EventRecord.module != "hermes",
                            EventRecord.severity.in_(_TRIGGER_SEVERITIES),
                        )
                        .order_by(EventRecord.timestamp)
                        .limit(self._settings.watchdog_max_events_per_check)
                    )
                )
                .scalars()
                .all()
            )
        return list(rows)

    async def _evaluate(self, event: EventRecord) -> None:
        situation = await self._situation_for(event)
        answer = await self._oracle.decide(situation)
        if not answer.ok:
            log.warning(
                "watchdog_decision_failed",
                extra={"event_id": event.id, "error": answer.error},
            )
            return
        decision = parse_decision(answer.reply)
        if decision is None or decision.action == "none":
            log.info(
                "watchdog_no_action",
                extra={"event_id": event.id, "type": f"{event.module}/{event.type}"},
            )
            return
        command = action_to_command(decision.action, decision.target)
        if command is None or not self._valid(decision, command):
            log.warning(
                "watchdog_rejected",
                extra={
                    "event_id": event.id,
                    "action": decision.action,
                    "target": decision.target,
                    "command": command,
                },
            )
            await self._log_event(
                "watchdog.rejected",
                "warning",
                f"Watchdog rejected proposal {decision.action} {decision.target}",
                reason=decision.reason,
                metadata={
                    "event_id": event.id,
                    "action": decision.action,
                    "target": decision.target,
                    "risk": decision.risk,
                },
            )
            return
        if self._cooldown_active(decision.target):
            log.info(
                "watchdog_cooldown",
                extra={"event_id": event.id, "target": decision.target},
            )
            return
        if not await self._container_exists(decision.target):
            # The model proposed an action against a container the daemon does
            # not know (hallucinated target, or an ephemeral job container that
            # already vanished). Never propose recovering something that is not
            # there — fail closed and audit the skip.
            log.warning(
                "watchdog_target_missing",
                extra={
                    "event_id": event.id,
                    "action": decision.action,
                    "target": decision.target,
                },
            )
            await self._log_event(
                "watchdog.skipped",
                "info",
                f"Watchdog skipped {decision.action} {decision.target}: container not found",
                reason=decision.reason,
                metadata={
                    "event_id": event.id,
                    "action": decision.action,
                    "target": decision.target,
                    "risk": decision.risk,
                },
            )
            return
        await self._propose(event, decision, command)

    def _valid(self, decision: WatchdogDecision, command: str | None) -> bool:
        if command is None or not _TARGET_RE.match(decision.target):
            return False
        patterns = self._settings.watchdog_command_patterns
        if not patterns:
            return False
        return any(fnmatch.fnmatch(command, pattern) for pattern in patterns)

    def _cooldown_active(self, target: str) -> bool:
        if target in self._pending:
            return True
        last = self._last_target.get(target)
        if last is None:
            return False
        now = datetime.now(UTC)
        return (now - last).total_seconds() < self._settings.watchdog_target_cooldown_seconds

    def _crashloop_count(self, target: str, now: datetime) -> int:
        """Executed watchdog actions for a target still inside the crash-loop window."""
        window = self._settings.watchdog_crashloop_window_seconds
        history = self._action_history.get(target, [])
        self._action_history[target] = [ts for ts in history if (now - ts).total_seconds() < window]
        return len(self._action_history[target])

    # -- Proposal lifecycle ----------------------------------------------------

    async def _propose(self, event: EventRecord, decision: WatchdogDecision, command: str) -> None:
        chat_id = self.primary_chat_id
        if chat_id is None:
            return
        self._last_target[decision.target] = datetime.now(UTC)
        pending = PendingAction(
            chat_id=chat_id,
            event_id=event.id,
            target=decision.target,
            action=decision.action,
            command=command,
            risk=local_risk(decision.action),
            reason=decision.reason,
            risk_model=decision.risk,
        )
        self._pending[decision.target] = pending

        component, prior = phoenix_prior_attempts(event, decision.action)
        crashloop = self._crashloop_count(decision.target, datetime.now(UTC))
        if prior:
            # Phoenix already exhausted this strategy on this component; never
            # auto-run the same failing action. Requires explicit approval.
            pending.risk = "medium"
            pending.risk_model = pending.risk_model or decision.risk
        elif crashloop >= self._settings.watchdog_crashloop_threshold:
            # The target keeps failing after repeated auto-actions: escalate so
            # the operator decides instead of burning more auto-restarts.
            pending.risk = "medium"
            pending.risk_model = pending.risk_model or decision.risk

        minutes = max(1, round(self._settings.watchdog_confirm_seconds / 60))
        if pending.risk == "low":
            body = (
                f"🤖 Watchdog: {event.module}/{event.type} — {event.title}\n\n"
                f"Proposed fix:\n<code>{command}</code>\n"
                f"Risk: low — {decision.reason}\n\n"
                f'I will run it in {minutes} min unless you reply "no". '
                'Reply "yes" to run it now.'
            )
            pending.task = asyncio.create_task(
                self._auto_proceed_after(pending), name=f"watchdog-window-{pending.target}"
            )
            pending.task.add_done_callback(
                lambda t: log.info(
                    "watchdog_window_done",
                    extra={
                        "target": pending.target,
                        "cancelled": t.cancelled(),
                        "exc": repr(t.exception()) if not t.cancelled() and t.exception() else None,
                    },
                )
            )
        else:
            warning = (
                f"⚠️ Phoenix already tried {decision.action} on this component "
                f"{prior} time(s) and it failed.\n\n"
                if prior
                else (
                    f"⚠️ {decision.target} has been {decision.action}-ed "
                    f"{crashloop} times by the watchdog in the last "
                    f"{self._settings.watchdog_crashloop_window_seconds / 60:.0f} min "
                    f"and is still failing (crash-looping).\n\n"
                    if crashloop >= self._settings.watchdog_crashloop_threshold
                    else ""
                )
            )
            body = (
                f"🤖 Watchdog: {event.module}/{event.type} — {event.title}\n\n"
                f"{warning}Proposed fix (needs your approval):\n<code>{command}</code>\n"
                f"Risk: {pending.risk} — {decision.reason}\n\n"
                'Reply "yes" to approve or "no" to reject.'
            )
        await self._notify(chat_id, body)
        await self._log_event(
            "watchdog.proposal",
            "info",
            f"Watchdog proposes {decision.action} {decision.target}",
            reason=decision.reason,
            metadata={
                "event_id": event.id,
                "action": decision.action,
                "target": decision.target,
                "command": command,
                "risk": pending.risk,
                "risk_model": pending.risk_model,
                "phoenix_prior_attempts": prior,
                "phoenix_component": component,
                "crashloop_actions": crashloop,
            },
        )

    async def _auto_proceed_after(self, pending: PendingAction) -> None:
        log.info(
            "watchdog_window_started",
            extra={
                "target": pending.target,
                "action": pending.action,
                "seconds": self._settings.watchdog_confirm_seconds,
            },
        )
        try:
            await asyncio.sleep(self._settings.watchdog_confirm_seconds)
        except asyncio.CancelledError:
            log.info(
                "watchdog_window_cancelled",
                extra={"target": pending.target, "action": pending.action},
            )
            return
        if pending.replied:
            log.info("watchdog_window_skipped", extra={"target": pending.target})
            return
        log.info(
            "watchdog_window_elapsed",
            extra={"target": pending.target, "action": pending.action},
        )
        # The window elapsed without a veto: proceed automatically.
        await self._execute(pending, source="auto")

    async def handle_user_reply(self, chat_id: int, text: str) -> bool:
        """Route an operator reply to the matching open proposal.

        Returns ``True`` when the message was consumed by a pending proposal
        (the bot then skips normal AI handling). ``False`` otherwise.
        """
        pending = self._open_pending(chat_id)
        if pending is None:
            return False
        word = text.strip().lower().rstrip(".!?").strip()
        if word in _APPROVE_WORDS:
            await self._execute(pending, source="operator")
        else:
            self._cancel(
                pending,
                note=f"operator instruction noted ({text!r})",
            )
            await self._notify(
                chat_id,
                f"🛑 Watchdog action cancelled per your reply.\n"
                f"(Proposed: <code>{pending.command}</code>)\n"
                f"Noted: {text}\nYou can run it manually with /cmd.",
            )
            await self._log_event(
                "watchdog.cancelled",
                "info",
                f"Watchdog action {pending.action} {pending.target} cancelled",
                metadata={
                    "target": pending.target,
                    "command": pending.command,
                    "risk": pending.risk,
                    "risk_model": pending.risk_model,
                    "reply": text[:200],
                },
            )
        return True

    def _open_pending(self, chat_id: int) -> PendingAction | None:
        candidates = [p for p in self._pending.values() if p.chat_id == chat_id and not p.replied]
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.created)

    async def _execute(self, pending: PendingAction, *, source: str) -> None:
        pending.replied = True
        self._pending.pop(pending.target, None)
        task = pending.task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        self._action_history.setdefault(pending.target, []).append(datetime.now(UTC))
        ok, output = await self._run_shell(pending.command)
        await self._notify(
            pending.chat_id,
            f"{'✅' if ok else '⚠️'} Watchdog {pending.action} {pending.target} "
            f"({source}){self._fmt_output(output, ok)}",
        )
        await self._log_event(
            "watchdog.action",
            "info" if ok else "warning",
            f"Watchdog {pending.action} {pending.target} ({'ok' if ok else 'failed'})",
            metadata={
                "target": pending.target,
                "action": pending.action,
                "command": pending.command,
                "risk": pending.risk,
                "risk_model": pending.risk_model,
                "source": source,
                "success": ok,
                "output": output[-500:],
            },
        )

    def _cancel(self, pending: PendingAction, note: str) -> None:
        pending.replied = True
        self._pending.pop(pending.target, None)
        if pending.task is not None:
            pending.task.cancel()
        log.info("watchdog_cancelled", extra={"target": pending.target, "note": note})

    def _fmt_output(self, output: str, ok: bool) -> str:
        text = (output or ("(no output)" if ok else "no output")).strip()
        if len(text) > 700:
            text = text[-700:] + "\n…(truncated)"
        if not ok:
            text = f"\n<pre>{text}</pre>"
        return f"\n{text}"

    # -- Shell / context -------------------------------------------------------

    async def _default_container_exists(self, name: str) -> bool:
        """True when ``name`` resolves to a container the daemon knows.

        Probes via the same shell channel used for actions; ``docker container
        inspect`` exits 0 only for known containers (running or stopped).
        Deterministic and fail-closed: a target the daemon cannot resolve is
        never a valid recovery target, whatever the model suggested.
        """
        if not _TARGET_RE.match(name):
            return False
        ok, _ = await self._run_shell(f"docker container inspect --format '{{{{.Id}}}}' {name}")
        return ok

    async def _run_shell(self, command: str) -> tuple[bool, str]:
        if self._run_command is not None:
            return await self._run_command(command, 60.0)
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=60)
        except TimeoutError:
            process.kill()
            await process.communicate()
            return False, "command timed out after 60s"
        output = stdout.decode(errors="replace").strip()
        return process.returncode in (0, None), output

    async def _situation_for(self, event: EventRecord) -> str:
        context = await build_ai_context(
            self._database, self._settings, self._queue, self._registry
        )
        recent = (
            ", ".join(
                f"{target} at {stamp.strftime('%H:%M')}"
                for target, stamp in sorted(self._last_target.items())
            )
            or "none"
        )
        stamp = event.timestamp.strftime("%m-%d %H:%M") if event.timestamp else "?"
        return "\n".join(
            [
                context,
                "",
                "Latest event triggering this check:",
                f"- {stamp} [{event.severity}] {event.module}/{event.type}",
                f"  title: {event.title}",
                f"  message: {event.message or '(none)'}",
                f"  metadata: {event.metadata_json or {}}",
                f"  tags: {event.tags or []}",
                "",
                "Recent actions you already took:",
                f"- {recent}",
            ]
        )

    async def _notify(self, chat_id: int, text: str) -> None:
        if self._bot is None:
            return
        await self._bot._reply(chat_id, text)

    # -- Audit trail -----------------------------------------------------------

    async def _log_event(
        self,
        type_: str,
        severity: str,
        title: str,
        *,
        reason: str = "",
        metadata: dict[str, Any],
    ) -> None:
        message = reason or title
        event = EventRecord(
            module="hermes",
            type=type_,
            severity=severity,
            title=title,
            message=message,
            metadata_json=metadata,
            tags=["watchdog", "ai"],
        )
        async with self._database.session_factory() as session:
            session.add(event)
            await session.commit()
            event_id = event.id
        self._queue.put(event_id)
        log.info(
            "watchdog_event",
            extra={"event_id": event_id, "type": type_, "severity": severity, "title": title},
        )

    # -- Introspection ----------------------------------------------------------

    def status(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        return {
            "enabled": self.enabled,
            "last_check": self._last_check.isoformat() if self._last_check else None,
            "evaluated_events": len(self._evaluated),
            "pending": [
                {
                    "target": p.target,
                    "action": p.action,
                    "risk": p.risk,
                    "command": p.command,
                }
                for p in self.pending
            ],
            "cooldowns": {
                target: int((now - stamp).total_seconds())
                for target, stamp in self._last_target.items()
            },
            "confirm_seconds": self._settings.watchdog_confirm_seconds,
        }
