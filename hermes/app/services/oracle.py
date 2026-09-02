from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import delete, select

from app.config.settings import Settings
from app.core.logging import get_logger
from app.database.models import ChatMessage
from app.database.session import Database

log = get_logger(__name__)

#: Roles that may be persisted as conversation turns.
_ALLOWED_ROLES = ("user", "assistant")


@dataclass(frozen=True)
class OracleAnswer:
    """Result of asking the Oracle gateway."""

    ok: bool
    reply: str = ""
    model: str | None = None
    error: str | None = None
    # Present when the answer came from the /v1/agent tool loop.
    steps: int = 0
    tools: list[dict[str, Any]] = field(default_factory=list)


class OracleClient:
    """Talks to the Oracle AI gateway (running on the laptop over the tailnet).

    Conversation history is persisted per chat in SQLite so follow-up
    questions keep their context across restarts. Hermes itself never loads
    models; it is only an HTTP client.
    """

    def __init__(
        self,
        settings: Settings,
        database: Database,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._database = database
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=settings.ai_timeout)

    @property
    def enabled(self) -> bool:
        return bool(self._settings.ai_enabled and self._settings.oracle_url)

    async def history(self, chat_id: int) -> list[dict[str, str]]:
        limit = max(0, self._settings.ai_max_history) * 2
        statement = (
            select(ChatMessage)
            .where(ChatMessage.chat_id == chat_id)
            .order_by(ChatMessage.created_at, ChatMessage.id)
        )
        async with self._database.session_factory() as session:
            rows = (
                (await session.execute(statement.limit(limit) if limit else statement))
                .scalars()
                .all()
            )
        return [{"role": row.role, "content": row.content} for row in rows]

    async def remember(self, chat_id: int, role: str, content: str) -> None:
        if role not in _ALLOWED_ROLES or not content:
            return
        async with self._database.session_factory() as session:
            session.add(ChatMessage(chat_id=chat_id, role=role, content=content[:10_000]))
            await session.commit()
            await self._prune(session, chat_id)

    async def forget(self, chat_id: int) -> None:
        async with self._database.session_factory() as session:
            await session.execute(delete(ChatMessage).where(ChatMessage.chat_id == chat_id))
            await session.commit()

    async def ask(self, chat_id: int, message: str, context: str | None = None) -> OracleAnswer:
        url = self._settings.oracle_url
        if not self._settings.ai_enabled or not url:
            return OracleAnswer(False, error="AI is not enabled")
        headers: dict[str, Any] = {"Content-Type": "application/json"}
        if self._settings.oracle_token:
            headers["Authorization"] = f"Bearer {self._settings.oracle_token}"
        payload: dict[str, Any] = {
            "message": message,
            "history": await self.history(chat_id),
        }
        if context:
            payload["context"] = context
        try:
            response = await self._client.post(
                f"{url.rstrip('/')}/v1/ask", json=payload, headers=headers
            )
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("oracle_unavailable", extra={"chat_id": chat_id, "error": str(exc)})
            return OracleAnswer(False, error=str(exc) or "unreachable")
        if response.status_code != 200 or not data.get("reply"):
            detail = data.get("detail") or f"HTTP {response.status_code}"
            log.warning("oracle_refused", extra={"chat_id": chat_id, "error": detail})
            return OracleAnswer(False, error=detail)
        reply = str(data["reply"]).strip()
        await self.remember(chat_id, "user", message)
        await self.remember(chat_id, "assistant", reply)
        return OracleAnswer(True, reply=reply, model=data.get("model"))

    async def agent(self, chat_id: int, message: str, context: str | None = None) -> OracleAnswer:
        """Ask the gateway's tool-calling loop (``/v1/agent``).

        The agent may execute Forge actions (container restarts, git ops, ...)
        under Forge's own policy and Level-1 Telegram approvals. Falls back to
        plain ``/v1/ask`` when the gateway does not expose the agent endpoint
        yet (404/405) or Forge is not configured there (503), so an outdated
        gateway degrades to chat instead of failing.
        """
        url = self._settings.oracle_url
        if not self._settings.ai_enabled or not url:
            return OracleAnswer(False, error="AI is not enabled")
        headers: dict[str, Any] = {"Content-Type": "application/json"}
        if self._settings.oracle_token:
            headers["Authorization"] = f"Bearer {self._settings.oracle_token}"
        payload: dict[str, Any] = {
            "message": message,
            "history": await self.history(chat_id),
        }
        if context:
            payload["context"] = context
        try:
            response = await self._client.post(
                f"{url.rstrip('/')}/v1/agent", json=payload, headers=headers
            )
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("oracle_agent_unavailable", extra={"chat_id": chat_id, "error": str(exc)})
            return OracleAnswer(False, error=str(exc) or "unreachable")
        if response.status_code in (404, 405, 503):
            log.info(
                "oracle_agent_fallback",
                extra={"chat_id": chat_id, "status": response.status_code},
            )
            return await self.ask(chat_id, message, context=context)
        if response.status_code != 200 or not data.get("reply"):
            detail = data.get("detail") or f"HTTP {response.status_code}"
            log.warning("oracle_agent_refused", extra={"chat_id": chat_id, "error": detail})
            return OracleAnswer(False, error=detail)
        reply = str(data["reply"]).strip()
        steps = int(data.get("steps") or 0)
        tools = data.get("tools") or []
        await self.remember(chat_id, "user", message)
        await self.remember(chat_id, "assistant", reply)
        return OracleAnswer(
            True,
            reply=reply,
            model=data.get("model"),
            steps=steps,
            tools=tools if isinstance(tools, list) else [],
        )

    async def decide(self, situation: str) -> OracleAnswer:
        """Ask the gateway for a structured watchdog decision.

        Stateless by design: the decision never enters ``chat_messages`` so the
        operator's conversation history stays clean. Parsing the JSON contract
        is the watchdog's job; here we only transport the raw model reply.
        """
        url = self._settings.oracle_url
        if not self._settings.ai_enabled or not url:
            return OracleAnswer(False, error="AI is not enabled")
        headers: dict[str, Any] = {"Content-Type": "application/json"}
        if self._settings.oracle_token:
            headers["Authorization"] = f"Bearer {self._settings.oracle_token}"
        payload: dict[str, Any] = {"situation": situation[:12_000]}
        try:
            response = await self._client.post(
                f"{url.rstrip('/')}/v1/decide", json=payload, headers=headers
            )
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("oracle_decide_unavailable", extra={"error": str(exc)})
            return OracleAnswer(False, error=str(exc) or "unreachable")
        if response.status_code != 200 or not data.get("reply"):
            detail = data.get("detail") or f"HTTP {response.status_code}"
            log.warning("oracle_decide_refused", extra={"error": detail})
            return OracleAnswer(False, error=detail)
        return OracleAnswer(True, reply=str(data["reply"]).strip(), model=data.get("model"))

    async def _prune(self, session, chat_id: int) -> None:
        """Keep at most ``ai_max_history`` turns per chat and nothing older than
        ``ai_history_max_age_days``."""
        max_age = max(0, self._settings.ai_history_max_age_days)
        if max_age:
            cutoff = datetime.now(UTC) - timedelta(days=max_age)
            await session.execute(
                delete(ChatMessage).where(
                    ChatMessage.chat_id == chat_id, ChatMessage.created_at < cutoff
                )
            )
        limit = max(0, self._settings.ai_max_history) * 2
        if limit:
            newest = (
                (
                    await session.execute(
                        select(ChatMessage.id)
                        .where(ChatMessage.chat_id == chat_id)
                        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            if newest:
                keep = set(newest)
                await session.execute(
                    delete(ChatMessage).where(
                        ChatMessage.chat_id == chat_id, ChatMessage.id.not_in(keep)
                    )
                )
            await session.commit()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
