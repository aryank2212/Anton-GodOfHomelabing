from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text

from app.config.settings import Settings
from app.core.logging import get_logger
from app.core.queue import NotificationQueue
from app.database.models import EventRecord
from app.database.session import Database
from app.providers.registry import ProviderRegistry

log = get_logger(__name__)

#: Chat/exchange events add noise to the model's view of system state.
_IGNORED_TYPES = ("bot.ask", "bot.command")


async def build_ai_context(
    database: Database,
    settings: Settings,
    queue: NotificationQueue,
    registry: ProviderRegistry,
) -> str:
    """Snapshot Hermes' live state for the Oracle gateway.

    Kept small and bounded so it fits comfortably in the model's context
    window. Queries are cheap (a handful of indexed counts) and run only when
    a question is asked.
    """
    now = datetime.now(UTC)
    since = now - timedelta(hours=1)

    db_ok = True
    try:
        async with database.session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    severity_counts: dict[str, int] = {}
    recent: list[str] = []
    async with database.session_factory() as session:
        if db_ok:
            rows = (
                await session.execute(
                    select(EventRecord.severity, func.count())
                    .where(EventRecord.timestamp >= since, EventRecord.type.not_in(_IGNORED_TYPES))
                    .group_by(EventRecord.severity)
                )
            ).all()
            severity_counts = {severity: int(count) for severity, count in rows}

            recent_rows = (
                (
                    await session.execute(
                        select(EventRecord)
                        .where(EventRecord.type.not_in(_IGNORED_TYPES))
                        .order_by(EventRecord.timestamp.desc(), EventRecord.id)
                        .limit(settings.ai_context_events)
                    )
                )
                .scalars()
                .all()
            )
            for row in recent_rows:
                stamp = row.timestamp.strftime("%m-%d %H:%M") if row.timestamp else "?"
                recent.append(f"- {stamp} [{row.severity}] {row.module}/{row.type}: {row.title}")

    total_1h = sum(severity_counts.values())
    by_severity = (
        ", ".join(
            f"{n} {sev}"
            for sev in ("error", "critical", "warning", "info")
            if (n := severity_counts.get(sev, 0))
        )
        or "none"
    )

    providers = ", ".join(p.name for p in registry.enabled) or "none"
    db_state = "ok" if db_ok else "error"
    lines = [
        f"Hermes live state at {now.isoformat(timespec='seconds')} UTC:",
        (
            f"- Version {settings.version} ({settings.environment}); "
            f"database {db_state}; queue {queue.size()}."
        ),
        f"- Notification providers enabled: {providers}.",
        f"- Events in the last hour: {total_1h} ({by_severity}).",
    ]
    if recent:
        lines.append("- Recent events:")
        lines.extend(recent)
    return "\n".join(lines)
