from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import update

from app.config.settings import Settings
from app.core.logging import get_logger
from app.core.renderer import Renderer
from app.database.models import EventRecord, NotificationRecord, RemediationRecord, event_to_dict
from app.database.session import Database
from app.providers.base import BaseProvider, ProviderMessage
from app.providers.registry import ProviderRegistry
from app.rules.engine import RuleDecision, RuleEngine
from app.rules.models import Remediation, RuleAction
from app.services.remediator import RemediationResult, Remediator

log = get_logger(__name__)


@dataclass(frozen=True)
class SendResult:
    success: bool
    attempts: int
    error: str | None


class Dispatcher:
    """Claims events from the queue and turns them into notifications.

    Runs entirely in the background worker; it never blocks an API request.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        registry: ProviderRegistry,
        engine: RuleEngine,
        renderer: Renderer,
        remediator: Remediator | None = None,
    ) -> None:
        self._settings = settings
        self._database = database
        self._registry = registry
        self._engine = engine
        self._renderer = renderer
        self._remediator = remediator or Remediator(settings=settings, renderer=renderer)

    async def process(self, event_id: str) -> None:
        prepared = await self._prepare(event_id)
        if prepared is None:
            return
        event, decision, targets, remediation = prepared

        remediation_result: RemediationResult | None = None
        if remediation is not None:
            remediation_result = await self._run_remediation(remediation, decision.rule, event)
            if not targets:
                outcome = "remediated" if remediation_result.success else "remediation_failed"
                await self._finish(event_id, outcome=outcome)
                return

        results = await asyncio.gather(*(self._send(target, event) for target in targets))
        await self._record_results(event_id, targets, results)
        outcome = _aggregate(results)
        if remediation_result is not None:
            outcome = "remediated" if remediation_result.success else "remediation_failed"
        await self._finish(event_id, outcome=outcome)

    async def _prepare(
        self, event_id: str
    ) -> (
        tuple[dict, RuleDecision, list[tuple[BaseProvider, NotificationRecord]], Remediation | None]
        | None
    ):
        async with self._database.session_factory() as session:
            event = await session.get(EventRecord, event_id)
            if event is None:
                log.warning("event_not_found", extra={"event_id": event_id})
                return None

            if not await self._claim(session, event_id):
                return None

            decision = self._engine.evaluate(event_to_dict(event))
            log.info(
                "event_routed",
                extra={
                    "event_id": event_id,
                    "event_module": event.module,
                    "type": event.type,
                    "severity": event.severity,
                    "action": decision.action.value,
                    "rule": decision.rule,
                },
            )

            if decision.action is RuleAction.IGNORE:
                await self._finish(event_id, outcome="ignored")
                return None
            if decision.action is RuleAction.LOG:
                await self._finish(event_id, outcome="logged")
                return None

            remediation: Remediation | None = None
            if decision.action is RuleAction.REMEDIATE:
                remediation = decision.remediation
                if remediation is None:
                    log.error(
                        "remediation_missing",
                        extra={"event_id": event_id, "rule": decision.rule},
                    )
                    await self._finish(event_id, outcome="failed")
                    return None

            targets = self._registry.resolve(list(decision.providers))
            notification_rows: list[NotificationRecord] = []
            for provider in targets:
                notification = NotificationRecord(
                    event_id=event_id,
                    provider=provider.name,
                    status="pending",
                )
                session.add(notification)
                notification_rows.append(notification)
            await session.commit()

            event_dict = event_to_dict(event)
            return (
                event_dict,
                decision,
                list(zip(targets, notification_rows, strict=False)),
                remediation,
            )

    async def _claim(self, session, event_id: str) -> bool:
        """Atomically move the event to 'processing'; idempotent under races."""
        result = await session.execute(
            update(EventRecord)
            .where(EventRecord.id == event_id, EventRecord.state == "pending")
            .values(state="processing")
        )
        await session.commit()
        return result.rowcount > 0

    async def _send(
        self,
        target: tuple[BaseProvider, NotificationRecord],
        event: dict,
    ) -> SendResult:
        provider, _notification = target
        attempts = 0
        last_error: str | None = None

        for attempt in range(1, self._settings.notification_max_attempts + 1):
            attempts = attempt
            try:
                rendered = self._renderer.render(event, provider.templates)
                message = ProviderMessage(event_id=event["id"], event=event, rendered=rendered)
                await provider.send(message)
                return SendResult(success=True, attempts=attempts, error=None)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning(
                    "provider_send_failed",
                    extra={
                        "provider": provider.name,
                        "event_id": event["id"],
                        "attempt": attempt,
                        "error": last_error,
                    },
                )
                if attempt < self._settings.notification_max_attempts:
                    await asyncio.sleep(
                        self._settings.notification_retry_base_delay * (2 ** (attempt - 1))
                    )
        return SendResult(success=False, attempts=attempts, error=last_error)

    async def _record_results(
        self,
        event_id: str,
        targets: list[tuple[BaseProvider, NotificationRecord]],
        results: list[SendResult],
    ) -> None:
        async with self._database.session_factory() as session:
            for (_, notification), result in zip(targets, results, strict=False):
                await session.execute(
                    update(NotificationRecord)
                    .where(NotificationRecord.id == notification.id)
                    .values(
                        status="sent" if result.success else "failed",
                        attempts=result.attempts,
                        error=result.error,
                    )
                )
            await session.commit()

    async def _run_remediation(
        self, remediation: Remediation, rule_name: str | None, event: dict
    ) -> RemediationResult:
        result = await self._remediator.run(remediation, event)
        async with self._database.session_factory() as session:
            session.add(
                RemediationRecord(
                    event_id=event["id"],
                    rule=rule_name or "?",
                    kind=remediation.kind,
                    target=_remediation_target(remediation),
                    status="done" if result.success else "failed",
                    attempts=1,
                    detail=result.detail,
                )
            )
            await session.commit()
        log.info(
            "remediation_executed",
            extra={
                "event_id": event["id"],
                "kind": remediation.kind,
                "success": result.success,
                "detail": result.detail,
            },
        )
        return result

    async def close(self) -> None:
        await self._remediator.close()

    async def _finish(self, event_id: str, outcome: str) -> None:
        async with self._database.session_factory() as session:
            await session.execute(
                update(EventRecord)
                .where(EventRecord.id == event_id)
                .values(
                    state="done",
                    outcome=outcome,
                    processed_at=datetime.now(UTC),
                )
            )
            await session.commit()


def _remediation_target(remediation: Remediation) -> str:
    if remediation.kind == "http":
        return remediation.url or ""
    if remediation.kind == "command":
        return remediation.command or ""
    if remediation.kind == "docker_restart":
        return remediation.container or ""
    return ""


def _aggregate(results: list[SendResult]) -> str:
    if not results:
        return "skipped"
    succeeded = sum(1 for result in results if result.success)
    if succeeded == len(results):
        return "notified"
    if succeeded == 0:
        return "failed"
    return "partial"
