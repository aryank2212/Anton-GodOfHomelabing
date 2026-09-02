from __future__ import annotations

import asyncio
import fnmatch
import html
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import func, select, text

from app.config.settings import Settings
from app.core.logging import get_logger
from app.core.queue import NotificationQueue
from app.database.models import EventRecord, NotificationRecord
from app.database.session import Database
from app.providers.registry import ProviderRegistry
from app.services.context import build_ai_context
from app.services.oracle import OracleAnswer, OracleClient

if TYPE_CHECKING:
    from app.services.watchdog import Watchdog

log = get_logger(__name__)

BOT_API = "https://api.telegram.org/bot{token}/{method}"

#: Columns summed for the Netdata system charts (mirrors scripts/netdata_health.py).
_RAM_TOTAL_COLUMNS = {"used", "free", "cached", "buffers"}
_DISK_TOTAL_COLUMNS = {"used", "avail", "reserved for root"}

SEVERITY_EMOJI = {
    "debug": "🔍",
    "info": "ℹ️",
    "warning": "⚠️",
    "error": "🔴",
    "critical": "🚨",
}


class TelegramBot:
    """Long-polling Telegram bot that lets operators talk to Hermes.

    Two-way channel: Hermes already *sends* notifications over Telegram; the
    bot adds *receiving* — commands such as ``/status``, ``/alerts`` and
    ``/events`` are answered from the Hermes database. Only the chat ids in
    ``HERMES_TELEGRAM_CHAT_ID`` are allowed to issue commands.

    Enabled when ``HERMES_BOT_ENABLED=true`` and the Telegram credentials are
    set. Every command is recorded as a ``hermes/bot.command`` event, so the
    normal rules (and storm detection) apply to bot traffic too.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        database: Database,
        queue: NotificationQueue,
        registry: ProviderRegistry,
        client: httpx.AsyncClient | None = None,
        oracle: OracleClient | None = None,
    ) -> None:
        self._settings = settings
        self._database = database
        self._queue = queue
        self._registry = registry
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=70.0)
        self._oracle = oracle or OracleClient(settings, database)
        self._task: asyncio.Task[None] | None = None
        self._offset: int | None = None
        self._running = False
        self._command_times: dict[int, list[float]] = {}
        self._watchdog: Watchdog | None = None
        self._approvals: Any = None

    def attach_watchdog(self, watchdog: Watchdog | None) -> None:
        """Bind the AI watchdog so plain-text replies can confirm proposals."""
        self._watchdog = watchdog

    def attach_approvals(self, approvals: Any) -> None:
        """Bind the Forge approval bridge so yes/no replies resolve approvals."""
        self._approvals = approvals

    @property
    def enabled(self) -> bool:
        return bool(
            self._settings.bot_enabled
            and self._settings.telegram_bot_token
            and self._settings.telegram_chat_id
        )

    @property
    def allowed_chats(self) -> list[int]:
        ids: list[int] = []
        for part in str(self._settings.telegram_chat_id).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                ids.append(int(part))
            except ValueError:
                log.warning("telegram_bot_invalid_chat_id", extra={"chat_id": part})
        return ids

    async def start(self) -> None:
        if not self.enabled:
            log.info("telegram_bot_disabled")
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop(), name="hermes-telegram-bot")
        log.info("telegram_bot_started", extra={"chats": self.allowed_chats})

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._owns_client:
            await self._client.aclose()
        await self._oracle.close()

    # -- Telegram long-polling ------------------------------------------------

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                for update in await self._get_updates():
                    self._offset = int(update["update_id"]) + 1
                    await self._handle_update(update)
            except Exception as exc:
                log.warning("telegram_bot_poll_error", extra={"error": str(exc)})
                await asyncio.sleep(5)

    async def _get_updates(self) -> list[dict[str, Any]]:
        token = self._settings.telegram_bot_token
        if not token:
            return []
        params: dict[str, Any] = {"timeout": 50, "allowed_updates": ["message"]}
        if self._offset is not None:
            params["offset"] = self._offset
        response = await self._client.get(
            BOT_API.format(token=token, method="getUpdates"), params=params
        )
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram getUpdates error: {data}")
        return list(data.get("result", []))

    async def _reply(self, chat_id: int, text: str, parse_mode: str | None = None) -> None:
        token = self._settings.telegram_bot_token
        if not token:
            return
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        response = await self._client.post(
            BOT_API.format(token=token, method="sendMessage"), json=payload
        )
        data = response.json()
        if not data.get("ok"):
            log.warning(
                "telegram_bot_reply_failed",
                extra={"chat_id": chat_id, "error": str(data.get("description"))},
            )

    # -- Inbound command handling ---------------------------------------------

    async def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id not in self.allowed_chats:
            log.info("telegram_bot_unauthorized", extra={"chat_id": chat_id})
            return
        text = str(message.get("text") or "")
        if not text:
            return
        if not text.startswith("/"):
            await self._handle_plain_text(chat_id, text)
            return
        if not self._rate_ok(chat_id):
            await self._reply(chat_id, "⏳ Too many commands. Try again in a moment.")
            return
        command, _, rest = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        reply, parse_mode = await self._dispatch(command, rest.strip())
        await self._reply(chat_id, reply, parse_mode)
        await self._log_command(chat_id, command)

    async def _handle_plain_text(self, chat_id: int, text: str) -> None:
        """A non-command message: route approval/watchdog replies or ask Oracle."""
        text = text.strip()
        if not text:
            return
        if self._approvals is not None:
            consumed = await self._approvals.handle_user_reply(chat_id, text)
            if consumed:
                return
        if self._watchdog is not None:
            consumed = await self._watchdog.handle_user_reply(chat_id, text)
            if consumed:
                return
        if not self._oracle.enabled:
            return
        if not self._rate_ok(chat_id):
            await self._reply(chat_id, "⏳ Too many requests. Try again in a moment.")
            return
        context = None
        if self._settings.ai_context_enabled:
            context = await build_ai_context(
                self._database, self._settings, self._queue, self._registry
            )
        if self._settings.ai_use_agent:
            answer = await self._oracle.agent(chat_id, text, context=context)
        else:
            answer = await self._oracle.ask(chat_id, text, context=context)
        if answer.ok:
            reply = self._truncate(answer.reply)
            if answer.tools:
                names = ", ".join(
                    tool.get("tool", "?") for tool in answer.tools if isinstance(tool, dict)
                )
                reply += f"\n\n_🔧 tool calls: {names}_"
            if answer.model:
                reply += f"\n\n_[{answer.model}]"
            await self._reply(chat_id, reply)
        else:
            await self._reply(
                chat_id,
                f"⚠️ Oracle unavailable: {answer.error}\nTry again later.",
            )
        await self._log_ask(chat_id, text.strip(), answer)

    def _rate_ok(self, chat_id: int) -> bool:
        now = time.monotonic()
        recent = [t for t in self._command_times.get(chat_id, []) if now - t < 60]
        recent.append(now)
        self._command_times[chat_id] = recent
        return len(recent) <= self._settings.bot_rate_limit_per_minute

    async def _dispatch(self, command: str, rest: str) -> tuple[str, str | None]:
        if command in ("/start", "/help"):
            return self._help(), None
        if command == "/status":
            return await self._cmd_status(), None
        if command == "/alerts":
            return (
                await self._cmd_recent("🚨 Recent alerts", rest, severities=("error", "critical")),
                None,
            )
        if command == "/events":
            return await self._cmd_recent("📋 Recent events", rest), None
        if command == "/providers":
            return self._cmd_providers(), None
        if command == "/health":
            return await self._cmd_health(), None
        if command == "/cmd":
            return await self._cmd_shell(rest)
        if command == "/watchdog":
            return self._cmd_watchdog(), None
        if command == "/argus":
            return await self._cmd_argus(), None
        if command == "/dots":
            return await self._cmd_dots(rest), None
        return self._help(), None

    # -- Command implementations ----------------------------------------------

    def _help(self) -> str:
        ai_line = (
            "\n\nJust send a message (no /) to ask Oracle, the local AI."
            if self._oracle.enabled
            else ""
        )
        return (
            "🟦 Hermes Telegram bot\n\n"
            "/status - Hermes health, queue and recent activity\n"
            "/alerts [n] - last n error/critical events (default 5)\n"
            "/events [n] - last n events (default 5)\n"
            "/providers - which notification providers are enabled\n"
            "/health - host health summary (Netdata)\n"
            "/cmd <cmd> - run a shell command (if enabled)\n"
            "/watchdog - AI monitor/manager status\n"
            "/argus - internet intelligence (Argus) health & evidence\n"
            "/dots <topic> - start a web investigation (/dots for recent, /dots <id> for status)\n"
            "/help - this help" + ai_line
        )

    async def _cmd_status(self) -> str:
        settings = self._settings
        db_ok = True
        try:
            async with self._database.session_factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            db_ok = False
        enabled = ", ".join(provider.name for provider in self._registry.enabled) or "none"
        alerts_1h = await self._count_events(hours=1, severities=("error", "critical"))
        return "\n".join(
            [
                f"🟦 Hermes {settings.version} ({settings.environment})",
                f"Database: {'ok' if db_ok else 'error'}",
                f"Queue size: {self._queue.size()}",
                f"Events (1h): {await self._count_events(hours=1)}",
                f"Alerts (1h): {alerts_1h}",
                f"Failed notifications (24h): {await self._count_failed_notifications(hours=24)}",
                f"Providers enabled: {enabled}",
            ]
        )

    async def _cmd_recent(
        self, heading: str, rest: str = "", severities: tuple[str, ...] | None = None
    ) -> str:
        limit = self._parse_limit(rest)
        rows = await self._recent_events(limit=limit, severities=severities)
        if not rows:
            return "✅ Nothing to report"
        lines = [heading]
        for row in rows:
            stamp = row.timestamp.strftime("%m-%d %H:%M") if row.timestamp else "?"
            lines.append(
                f"{stamp} {SEVERITY_EMOJI.get(row.severity, '')}"
                f"[{row.severity.upper()}] {row.module}/{row.type}: {row.title}"
            )
        return "\n".join(lines)

    def _parse_limit(self, rest: str) -> int:
        try:
            limit = int(rest)
        except ValueError:
            return 5
        return max(1, min(limit, 50))

    def _cmd_providers(self) -> str:
        enabled = [provider.name for provider in self._registry.enabled]
        lines = ["🔌 Notification providers:"]
        lines.extend(f"{'🟢' if name in enabled else '⚪'} {name}" for name in self._registry.names)
        return "\n".join(lines)

    def _cmd_watchdog(self) -> str:
        if self._watchdog is None:
            return "🐕 Watchdog: not attached"
        status = self._watchdog.status()
        if not status["enabled"]:
            return (
                "🐕 Watchdog: disabled\n"
                "Set HERMES_WATCHDOG_ENABLED=true (plus AI + bot) to enable."
            )
        lines = [
            "🐕 Watchdog (AI monitor/manager):",
            f"Last check: {status['last_check'] or 'never'}",
            f"Events evaluated: {status['evaluated_events']}",
            f"Confirm window: {status['confirm_seconds']:g}s",
        ]
        if status["pending"]:
            lines.append("Pending proposals:")
            lines.extend(
                f"- {p['risk']} {p['action']} {p['target']}: {p['command']}"
                for p in status["pending"]
            )
        else:
            lines.append("Pending proposals: none")
        cooldowns = status["cooldowns"]
        if cooldowns:
            lines.append("Cooldowns (target: seconds ago):")
            lines.extend(f"- {target}: {seconds}" for target, seconds in cooldowns.items())
        else:
            lines.append("Cooldowns: none")
        return "\n".join(lines)

    async def _cmd_health(self) -> str:
        try:
            summary = await self._netdata_summary()
        except Exception as exc:
            return (
                f"⚠️ Netdata unreachable: {exc}\n"
                "Set HERMES_NETDATA_URL to a host-reachable Netdata "
                "(from the container, e.g. http://host.docker.internal:19999)."
            )
        return summary

    async def _cmd_argus(self) -> str:
        base = self._settings.bot_argus_url.rstrip("/")
        try:
            response = await self._client.get(f"{base}/v1/health", timeout=10.0)
            response.raise_for_status()
            health = response.json()
        except Exception as exc:
            return (
                f"⚠️ Argus unreachable: {exc}\n"
                f"Set HERMES_BOT_ARGUS_URL to a host-reachable Argus "
                "(from the container, e.g. http://host.docker.internal:8012)."
            )
        status = health.get("status", "unknown")
        stamp = health.get("last_intelligence_tick")
        if stamp:
            with suppress(ValueError):
                stamp = datetime.fromisoformat(stamp).strftime("%m-%d %H:%M")
        hermes = health.get("hermes", {})
        oracle = health.get("oracle", {})
        lines = [
            f"🟦 Argus {health.get('version', 'unknown')} ({health.get('environment', '?')})",
            f"Status: {'🟢 ok' if status == 'ok' else '🔴 ' + status}",
            f"Uptime: {health.get('uptime_seconds', 0):.0f}s",
            f"Evidence: {health.get('evidence', 0)}",
            f"Entities: {health.get('entities', 0)}",
        ]
        collectors = health.get("collectors", {})
        if collectors.get("enabled"):
            lines.append(f"Collectors: {collectors.get('count', 0)} active")
        lines.append(f"Hermes link: {'connected' if hermes.get('enabled') else 'off'}")
        if oracle.get("enabled"):
            lines.append(f"Oracle: {oracle.get('base_url', '?')}")
        if stamp:
            lines.append(f"Last report sweep: {stamp}")
        return "\n".join(lines)

    async def _cmd_shell(self, rest: str) -> tuple[str, str]:
        if not self._settings.bot_allow_cmd:
            return "⛔ /cmd is disabled (set HERMES_BOT_ALLOW_CMD=true)", "HTML"
        if not rest:
            return self._pre("Usage: /cmd <shell command>\nExample: /cmd ls -la"), "HTML"
        patterns = self._settings.bot_command_patterns
        if patterns and not any(fnmatch.fnmatch(rest, pattern) for pattern in patterns):
            return (
                self._pre(
                    "⛔ Command not allowed.\n"
                    f"Allowed patterns: {', '.join(patterns)}\n"
                    "See HERMES_BOT_ALLOWED_CMDS in .env."
                ),
                "HTML",
            )
        try:
            process = await asyncio.create_subprocess_shell(
                rest,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=60)
        except TimeoutError:
            process.kill()
            await process.communicate()
            return self._pre("Command timed out after 60s."), "HTML"
        output = stdout.decode(errors="replace").strip()
        output = output or f"(exit code {process.returncode}, no output)"
        output += f"\n[exit code {process.returncode}]"
        return self._pre(output), "HTML"

    # -- Dots (Argus internet investigations) ----------------------------------

    async def _cmd_dots(self, rest: str) -> str:
        base = self._settings.bot_argus_url.rstrip("/")
        if not rest:
            return await self._dots_list(base)
        iterations: int | None = None
        tokens: list[str] = []
        for token in rest.split():
            if token.lower().startswith("--i="):
                try:
                    iterations = int(token.split("=", 1)[1])
                except ValueError:
                    iterations = None
                if iterations is not None and not 1 <= iterations <= 30:
                    return "⛔ iterations must be 1..30 (try /dots <topic> --i=8)"
            else:
                tokens.append(token)
        if not tokens:
            return (
                "🚀 /dots usage:\n"
                "/dots — list recent runs\n"
                "/dots <topic> [--i=N] — start a dots investigation\n"
                "/dots <run-id> — run status & summary"
            )
        topic = " ".join(tokens)
        candidate = topic.split()[0]
        if len(candidate) == 36 and candidate.count("-") == 4:
            return await self._dots_run(base, candidate)
        return await self._dots_start(base, topic, iterations)

    async def _dots_list(self, base: str) -> str:
        try:
            response = await self._client.get(
                f"{base}/v1/dots", params={"limit": 5}, timeout=10.0
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            return self._dots_unreachable(exc)
        items = data.get("items") or []
        if not items:
            return "🌱 No dot runs yet. Try /dots <topic>"
        lines = [f"🟦 Recent dot runs ({data.get('total', len(items))} total):"]
        lines.extend(self._dots_row(row) for row in items)
        return "\n".join(lines)

    async def _dots_start(self, base: str, topic: str, iterations: int | None) -> str:
        body: dict[str, Any] = {"topic": topic}
        if iterations is not None:
            body["iterations"] = iterations
        try:
            response = await self._client.post(f"{base}/v1/dots", json=body, timeout=10.0)
            data = response.json()
            if response.status_code != 202:
                return f"⛔ Argus refused: {data.get('detail', response.text)}"
        except Exception as exc:
            return self._dots_unreachable(exc)
        run_id = data.get("dot_run_id", "?")
        target = data.get("iterations_target", iterations or "default")
        return (
            f"🚀 Dots run started: {data.get('topic')}\n"
            f"`{run_id}`\n"
            f"Status: queued · up to {target} iterations\n\n"
            "The intelligence report will be pushed here when it's done."
        )

    async def _dots_run(self, base: str, run_id: str) -> str:
        try:
            response = await self._client.get(f"{base}/v1/dots/{run_id}", timeout=10.0)
            if response.status_code == 404:
                return f"🤷 No dot run with id `{run_id}`"
            response.raise_for_status()
            row = response.json()
        except Exception as exc:
            return self._dots_unreachable(exc)
        lines = self._dots_row(row).splitlines()
        if row.get("status") == "completed" and (row.get("summary") or "").strip():
            lines.append(f"\n💡 {self._truncate(str(row['summary']))}")
        elif row.get("status") == "failed" and row.get("error"):
            lines.append(f"\n🆘 {self._truncate(str(row['error']))}")
        return "\n".join(lines)

    def _dots_row(self, row: dict[str, Any]) -> str:
        status = str(row.get("status") or "")
        emoji = {
            "queued": "⏳",
            "running": "🔄",
            "completed": "✅",
            "failed": "❌",
            "cancelled": "⛔",
        }.get(status, "•")
        run_id = str(row.get("dot_run_id", "?"))[:8]
        topic = str(row.get("topic", "?"))[:44]
        progress = f"[{row.get('iterations_done', 0)}/{row.get('iterations_target', 0)}]"
        stats = f"{row.get('dots_kept', 0)} dots, {row.get('evidence_count', 0)} ev"
        created = row.get("created_at", "")
        with suppress(ValueError):
            created = datetime.fromisoformat(created).strftime("%m-%d %H:%M")
        return (
            f"{emoji} {topic}\n"
            f"    {run_id} | {row.get('status')} {progress} | {stats} | {created}"
        )

    def _dots_unreachable(self, exc: Exception) -> str:
        return (
            f"⚠️ Argus unreachable: {exc}\n"
            "Set HERMES_BOT_ARGUS_URL to a host-reachable Argus "
            "(e.g. http://host.docker.internal:8012)."
        )

    # -- Netdata ----------------------------------------------------------------

    async def _netdata_summary(self) -> str:
        base = self._settings.netdata_url.rstrip("/")
        response = await self._client.get(f"{base}/api/v1/info", timeout=10.0)
        response.raise_for_status()
        info = response.json()
        alarms = info.get("alarms", {}) or {}
        cpu_used = await self._netdata_total(base, "system.cpu", None)
        ram_used = await self._netdata_total(base, "system.ram", {"used"})
        ram_total = await self._netdata_total(base, "system.ram", _RAM_TOTAL_COLUMNS)
        disk_used = await self._netdata_total(base, "disk_space./", {"used"})
        disk_total = await self._netdata_total(base, "disk_space./", _DISK_TOTAL_COLUMNS)

        warning = int(alarms.get("warning", 0))
        critical = int(alarms.get("critical", 0))
        if critical:
            alarm_line = f"🔴 {critical} critical"
            if warning:
                alarm_line += f", ⚠️ {warning} warning"
        elif warning:
            alarm_line = f"⚠️ {warning} warning"
        else:
            alarm_line = "🟢 all clear"

        def pct(part: float, whole: float) -> float:
            return part / whole * 100 if whole else 0.0

        ram_pct = pct(ram_used, ram_total)
        disk_pct = pct(disk_used, disk_total)
        return "\n".join(
            [
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                f"CPU: {min(cpu_used, 100):.0f}%",
                f"RAM: {ram_used / 1024:.1f} / {ram_total / 1024:.1f} GiB ({ram_pct:.0f}%)",
                f"Disk /: {disk_used:.1f} / {disk_total:.1f} GiB ({disk_pct:.0f}%)",
                f"Alerts: {alarm_line}",
                f"Netdata: {info.get('version', 'unknown')}",
            ]
        )

    async def _netdata_total(self, base: str, chart: str, columns: set[str] | None) -> float:
        params: dict[str, Any] = {"chart": chart, "after": -60, "before": 0, "points": 1}
        response = await self._client.get(f"{base}/api/v1/data", params=params, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        labels = list(data.get("labels", []))[1:]
        row = list((data.get("data") or [[]])[0][1:])
        return sum(
            value
            for label, value in zip(labels, row, strict=False)
            if isinstance(value, (int, float)) and (columns is None or label in columns)
        )

    # -- Helpers ----------------------------------------------------------------

    async def _recent_events(
        self, *, limit: int, severities: tuple[str, ...] | None = None
    ) -> list[EventRecord]:
        statement = select(EventRecord).order_by(EventRecord.timestamp.desc(), EventRecord.id)
        if severities:
            statement = statement.where(EventRecord.severity.in_(severities))
        async with self._database.session_factory() as session:
            rows = (await session.execute(statement.limit(limit))).scalars().all()
        return list(rows)

    async def _count_events(self, *, hours: int, severities: tuple[str, ...] | None = None) -> int:
        since = datetime.now(UTC) - timedelta(hours=hours)
        statement = (
            select(func.count()).select_from(EventRecord).where(EventRecord.timestamp >= since)
        )
        if severities:
            statement = statement.where(EventRecord.severity.in_(severities))
        async with self._database.session_factory() as session:
            return (await session.execute(statement)).scalar_one()

    async def _count_failed_notifications(self, *, hours: int) -> int:
        since = datetime.now(UTC) - timedelta(hours=hours)
        statement = (
            select(func.count())
            .select_from(NotificationRecord)
            .where(NotificationRecord.status == "failed", NotificationRecord.updated_at >= since)
        )
        async with self._database.session_factory() as session:
            return (await session.execute(statement)).scalar_one()

    async def _log_command(self, chat_id: int, command: str) -> None:
        event = EventRecord(
            module="hermes",
            type="bot.command",
            severity="info",
            title=f"Bot command {command}",
            message=command,
            metadata_json={"chat_id": chat_id, "command": command},
            tags=["bot", "telegram"],
        )
        async with self._database.session_factory() as session:
            session.add(event)
            await session.commit()
            event_id = event.id
        self._queue.put(event_id)

    async def _log_ask(self, chat_id: int, question: str, answer: OracleAnswer) -> None:
        event = EventRecord(
            module="hermes",
            type="bot.ask",
            severity="info" if answer.ok else "warning",
            title="AI question" if answer.ok else "AI question failed",
            message=question,
            metadata_json={
                "chat_id": chat_id,
                "model": answer.model,
                "ok": answer.ok,
                "error": answer.error,
            },
            tags=["bot", "ai"],
        )
        async with self._database.session_factory() as session:
            session.add(event)
            await session.commit()
            event_id = event.id
        self._queue.put(event_id)

    def _pre(self, text: str) -> str:
        escaped = html.escape(self._truncate(text))
        block = f"<pre>{escaped}</pre>"
        if len(block) > 4000:
            block = f"<pre>{escaped[:3980]}\n...[truncated]</pre>"
        return block

    def _truncate(self, text: str) -> str:
        limit = self._settings.bot_max_output_chars
        if len(text) <= limit:
            return text
        return text[:limit] + "\n...[truncated]"
