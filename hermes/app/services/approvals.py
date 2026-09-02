from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from app.config.settings import Settings
from app.core.logging import get_logger
from app.core.queue import NotificationQueue
from app.database.models import EventRecord
from app.database.session import Database

if TYPE_CHECKING:
    from app.bot.telegram_bot import TelegramBot

log = get_logger(__name__)

#: Words that approve a pending Forge action.
_APPROVE_WORDS = {
    "yes",
    "y",
    "yeah",
    "yep",
    "ok",
    "okay",
    "approve",
    "approved",
    "confirm",
    "do it",
    "go ahead",
}
#: Words that reject a pending Forge action.
_REJECT_WORDS = {"no", "n", "nope", "nah", "reject", "deny", "cancel", "veto", "stop", "abort"}


@dataclass
class PendingApproval:
    """One action awaiting an operator thumbs-up over Telegram."""

    approval_id: str
    chat_id: int
    text: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class ApprovalBridge:
    """Relays Forge approval requests to Telegram and routes the operator's
    reply back to Forge's resolve endpoint.

    The bridge is deliberately dumb: it does not judge an action, it only
    transports the request (Forge -> operator) and the verdict (operator ->
    Forge). ``handle_user_reply`` returns ``True`` when a message was consumed
    by a pending approval so the bot skips normal AI handling.
    """

    def __init__(
        self,
        settings: Settings,
        database: Database,
        *,
        queue: NotificationQueue | None = None,
        bot: TelegramBot | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._database = database
        self._queue = queue
        self._bot = bot
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=20.0)
        self._pending: dict[str, PendingApproval] = {}
        self._task: asyncio.Task[None] | None = None
        self._running = False

    def attach_bot(self, bot: TelegramBot | None) -> None:
        """Bind the Telegram bot used for outbound messages (like watchdog)."""
        self._bot = bot

    @property
    def enabled(self) -> bool:
        return bool(
            self._settings.forge_enabled
            and self._settings.forge_url
            and self._bot is not None
            and self._bot.enabled
        )

    @property
    def pending(self) -> list[PendingApproval]:
        return sorted(self._pending.values(), key=lambda p: p.created_at, reverse=True)

    @property
    def primary_chat_id(self) -> int | None:
        if self._bot is None:
            return None
        chats = self._bot.allowed_chats
        return chats[0] if chats else None

    async def start(self) -> None:
        if not self.enabled:
            log.info("approval_bridge_disabled")
            return
        self._running = True
        self._task = asyncio.create_task(self._sweep_loop(), name="hermes-approval-bridge")
        log.info("approval_bridge_started", extra={"chat_id": self.primary_chat_id})

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._owns_client:
            await self._client.aclose()

    # -- Inbound from Forge --------------------------------------------------

    async def request_approval(self, approval_id: str, text: str) -> bool:
        """Store a pending approval and send it to Telegram. Returns whether the
        message was delivered (the caller — Forge — fails closed otherwise)."""
        chat_id = self.primary_chat_id
        if chat_id is None or not self._settings.forge_url:
            return False
        self._pending[approval_id] = PendingApproval(
            approval_id=approval_id, chat_id=chat_id, text=text
        )
        minutes = max(1, round(self._settings.forge_approval_timeout / 60))
        body = (
            f"🔐 Forge approval needed\n\n{text}\n\n"
            f'Reply <b>"yes"</b> to approve, <b>"no"</b> to reject. '
            f"Expires in {minutes} min (id <code>{approval_id}</code>)."
        )
        await self._notify(chat_id, body)
        await self._log_event(
            "forge.approval.requested",
            "info",
            f"Forge approval requested: {approval_id}",
            metadata={"approval_id": approval_id, "chat_id": chat_id},
        )
        return True

    # -- Outbound to Forge ----------------------------------------------------

    async def handle_user_reply(self, chat_id: int, text: str) -> bool:
        """Route an operator yes/no to the newest pending approval for the chat.

        Returns ``True`` when the message was consumed by a pending approval
        (the bot then skips Oracle handling), ``False`` otherwise.
        """
        word = text.strip().lower().rstrip(".!?").strip()
        pending = self._open_pending(chat_id)
        if pending is None:
            return False
        if word in _APPROVE_WORDS:
            await self._resolve(pending, approved=True)
            return True
        if word in _REJECT_WORDS:
            await self._resolve(pending, approved=False)
            return True
        return False

    def _open_pending(self, chat_id: int) -> PendingApproval | None:
        candidates = [p for p in self._pending.values() if p.chat_id == chat_id]
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.created_at)

    async def _resolve(self, pending: PendingApproval, *, approved: bool) -> None:
        self._pending.pop(pending.approval_id, None)
        url = self._settings.forge_url or ""
        if not url:
            return
        url = f"{url.rstrip('/')}/v1/approvals/{pending.approval_id}/resolve"
        headers: dict[str, Any] = {"Content-Type": "application/json"}
        if self._settings.forge_token:
            headers["Authorization"] = f"Bearer {self._settings.forge_token}"
        try:
            response = await self._client.post(
                url, json={"approved": approved, "by": "telegram"}, headers=headers
            )
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning(
                "forge_resolve_unreachable",
                extra={"approval_id": pending.approval_id, "error": str(exc)},
            )
            await self._notify(
                pending.chat_id,
                "⚠️ Forge is unreachable — your reply was not delivered. Try again shortly.",
            )
            return
        ok = response.status_code < 400
        outcome = data if isinstance(data, dict) else {}
        if approved:
            message = f"✅ Approved <code>{pending.approval_id}</code>.\n" + (
                f"Forge says: <code>{outcome.get('output', '')}</code>"
                if ok
                else f"⚠️ Forge: {outcome.get('detail', f'HTTP {response.status_code}')}"
            )
        else:
            message = f"❌ Rejected <code>{pending.approval_id}</code> — nothing was run."
        await self._notify(pending.chat_id, message)
        await self._log_event(
            "forge.approval.resolved",
            "info" if ok else "warning",
            f"Forge approval {'approved' if approved else 'rejected'}: {pending.approval_id}",
            metadata={
                "approval_id": pending.approval_id,
                "approved": approved,
                "ok": ok,
                "output": outcome.get("output") if ok else None,
                "error": outcome.get("detail") if not ok else None,
            },
        )

    # -- Maintenance ----------------------------------------------------------

    async def _sweep_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._settings.forge_approval_sweep_interval)
            await self._sweep()

    async def _sweep(self) -> None:
        for pending in list(self._pending.values()):
            age = (datetime.now(UTC) - pending.created_at).total_seconds()
            if age < self._settings.forge_approval_timeout:
                continue
            self._pending.pop(pending.approval_id, None)
            await self._notify(
                pending.chat_id,
                f"⏰ Forge approval <code>{pending.approval_id}</code> expired unhandled.",
            )
            await self._log_event(
                "forge.approval.expired",
                "info",
                f"Forge approval expired: {pending.approval_id}",
                metadata={"approval_id": pending.approval_id},
            )

    # -- Plumbing --------------------------------------------------------------

    async def _notify(self, chat_id: int, text: str) -> None:
        if self._bot is not None:
            await self._bot._reply(chat_id, text)

    async def _log_event(
        self, type_: str, severity: str, title: str, *, metadata: dict[str, Any]
    ) -> None:
        event = EventRecord(
            module="hermes",
            type=type_,
            severity=severity,
            title=title,
            message=title,
            metadata_json=metadata,
            tags=["forge", "approval"],
        )
        async with self._database.session_factory() as session:
            session.add(event)
            await session.commit()
            event_id = event.id
        if self._queue is not None:
            self._queue.put(event_id)
        log.info(
            "approval_bridge_event",
            extra={"event_id": event_id, "type": type_, "severity": severity, "title": title},
        )
