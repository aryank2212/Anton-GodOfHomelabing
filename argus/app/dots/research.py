"""Research worker — advances goal-directed research sessions.

A research session is one question decomposed into a few research angles, each
of which is executed as a normal dot investigation. When every angle has been
investigated, the coordinator asks Oracle to synthesise the findings into a
final research report.

Three research modes steer how the session runs:

- single_pass: all angles are planned up front, run once, synthesised at the
  end.
- progressive: only the first angle is planned; each next angle is re-planned
  once the previous one completes, so later angles build on earlier findings.
- contradictory: angles that build the case FOR the plausible answer are run
  alongside counter-angles that deliberately attack it; the synthesis weighs
  both sides.

On top of the modes, an adaptive loop drives every session: once the current
angles are settled, Oracle assesses the evidence — if it judges the picture
incomplete it supplies another round of angles, and the session keeps looping
(up to ``research_max_rounds``) until Oracle calls it complete or the rounds
budget is spent.

The coordinator drives sessions serially (oldest first) and enqueues at most
one dot run per tick, so it cooperates cleanly with the shared serial dots
worker: it never floods the queue, and it never reasons itself — every
judgement (angles, next angle, synthesis) is an Oracle call.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from app.config.settings import Settings
from app.core.logging import get_logger
from app.core.oracle import OracleClient
from app.core.publisher import EventPublisher
from app.database.repository import Repository
from app.dots.engine import DotEnqueueError, DotsEngine
from app.dots.researcher import DotResearcher
from app.models.base import utcnow
from app.models.dots import DotRun, DotRunStatus
from app.models.event import EVENT_REPORT_PUBLISHED, HermesEvent
from app.models.report import Report, ReportStatus
from app.models.research import (
    ResearchSession,
    ResearchSessionMode,
    ResearchSessionStatus,
)

log = get_logger(__name__)

_ACTIVE = (DotRunStatus.QUEUED, DotRunStatus.RUNNING)


class ResearchCoordinator:
    """Serial scheduler and synthesizer for research sessions."""

    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        oracle: OracleClient,
        researcher: DotResearcher,
        dots: DotsEngine,
        publisher: EventPublisher | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._oracle = oracle
        self._researcher = researcher
        self._dots = dots
        self._publisher = publisher

    # ---------------------------------------------------------------- driver
    async def tick(self) -> None:
        """Advance one session; never raises, never stalls the runtime."""
        try:
            await self._advance()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a bad tick must not kill the worker
            log.error("research_tick_failed", extra={"error": str(exc)})

    async def _advance(self) -> None:
        repository = self._repository
        sessions = await repository.oldest_active_research_session(limit=1)
        if not sessions:
            return
        session = sessions[0]

        if session.status == ResearchSessionStatus.QUEUED:
            await self._plan_and_start(session)

        runs = await repository.list_dot_runs_for_session(session.research_session_id)
        if any(run.status in _ACTIVE for run in runs):
            return  # the serial dots worker is already on one of these

        if await self._enqueue_unattempted(session, runs):
            return

        if await self._retry_failed(session, runs):
            return

        if await self._plan_next_progressive(session, runs):
            return

        if await self._adaptive_round(session, runs):
            return

        await self._finish(session, runs)

    # ------------------------------------------------------------------- plan
    async def _plan_and_start(self, session: ResearchSession) -> None:
        plan = await self._researcher.plan_research(
            session.question,
            session.context,
            mode=session.mode.value,
            max_angles=session.max_angles,
        )
        session.angles_planned = self._initial_angles(plan, session)
        session.status = ResearchSessionStatus.RUNNING
        session.started_at = utcnow()
        session.updated_at = utcnow()
        session.metadata["rounds"] = 0
        await self._repository.save_research_session(session)
        log.info(
            "research_session_started",
            extra={
                "session": str(session.research_session_id),
                "mode": session.mode.value,
                "angles": len(session.angles_planned),
                "question": session.question[:200],
            },
        )
        await self._enqueue(session, session.angles_planned[0])

    @staticmethod
    def _initial_angles(
        plan: dict[str, Any], session: ResearchSession
    ) -> list[str]:
        """Choose which angles a session starts with, per mode.

        progressive sessions start with a single foundation angle and re-plan
        as findings arrive; contradictory sessions allocate the angle budget
        between supporting angles and counter-angles.
        """
        max_angles = session.max_angles
        angles = (plan.get("angles") or [])[:max_angles]
        counter = (plan.get("counter_angles") or [])[: max(0, max_angles - len(angles))]
        if session.mode == ResearchSessionMode.PROGRESSIVE:
            return angles[:1]
        if session.mode == ResearchSessionMode.CONTRADICTORY:
            planned = angles + counter
            session.metadata["counter_angles"] = counter
            return planned
        return angles

    async def _plan_next_progressive(
        self, session: ResearchSession, runs: list[DotRun]
    ) -> bool:
        """Re-plan the next angle for a progressive session, if one is due."""
        if session.mode != ResearchSessionMode.PROGRESSIVE:
            return False
        if len(session.angles_planned) >= session.max_angles:
            return False
        views = self._completed_views(runs)
        if not views:
            return False
        angle = await self._researcher.plan_next_angle(
            session.question, session.context, views
        )
        if not angle or angle in session.angles_planned:
            return False
        session.angles_planned.append(angle)
        session.updated_at = utcnow()
        await self._repository.save_research_session(session)
        await self._enqueue(session, angle)
        return True

    async def _gain_stops_round(
        self, session: ResearchSession, runs: list[DotRun], next_angles: list[str]
    ) -> bool:
        """True when the pending round is not worth the Oracle's rated gain.

        The information-gain stopping rule: when the marginal contribution of a
        planned round falls below ``research_min_information_gain``, the session
        ends instead of running a round Oracle expects to add little. The score
        is recorded so operators can audit why a session stopped early.
        """
        threshold = self._settings.research_min_information_gain
        if threshold <= 0:
            return False
        gain = await self._researcher.expected_gain(
            session.question,
            session.context,
            self._completed_views(runs),
            mode=session.mode.value,
            next_angles=next_angles,
        )
        session.metadata["info_gain"] = round(gain, 3)
        if gain >= threshold:
            return False
        session.metadata["stopped_low_information_gain"] = True
        session.updated_at = utcnow()
        await self._repository.save_research_session(session)
        log.info(
            "research_stopped_low_information_gain",
            extra={
                "session": str(session.research_session_id),
                "gain": round(gain, 3),
                "threshold": threshold,
            },
        )
        return True

    async def _adaptive_round(
        self, session: ResearchSession, runs: list[DotRun]
    ) -> bool:
        """Plan another round when Oracle judges the evidence incomplete.

        Runs for every mode once all current angles are settled: the assessor
        decides whether the session is complete, and — when not — supplies the
        next round of angles. Rounds are capped by ``research_max_rounds`` so a
        talkative Oracle cannot loop a session forever, Oracle-repeated angles
        are discarded, and the information-gain rule may end the session early
        when the pending round would add little new information.
        """
        completed = [run for run in runs if run.status == DotRunStatus.COMPLETED]
        if not completed:
            return False
        rounds = int(session.metadata.get("rounds") or 0)
        if rounds >= self._settings.research_max_rounds - 1:
            return False

        assessment = await self._researcher.assess_session(
            session.question,
            session.context,
            self._completed_views(completed),
            mode=session.mode.value,
        )
        session.metadata["assessment"] = str(assessment.get("assessment") or "")
        if assessment.get("complete") is True:
            session.updated_at = utcnow()
            await self._repository.save_research_session(session)
            return False

        attempted = set(session.angles_planned)
        next_angles = [
            angle
            for angle in (assessment.get("next_angles") or [])
            if angle and angle not in attempted
        ]
        if not next_angles:
            session.updated_at = utcnow()
            await self._repository.save_research_session(session)
            return False

        if await self._gain_stops_round(session, runs, next_angles):
            return False

        session.angles_planned.extend(next_angles)
        session.metadata["rounds"] = rounds + 1
        session.updated_at = utcnow()
        await self._repository.save_research_session(session)
        log.info(
            "research_round_planned",
            extra={
                "session": str(session.research_session_id),
                "round": session.metadata["rounds"],
                "angles": len(next_angles),
            },
        )
        await self._enqueue(session, next_angles[0])
        return True

    async def _enqueue_unattempted(
        self, session: ResearchSession, runs: list[DotRun]
    ) -> bool:
        attempted = {run.topic for run in runs}
        for angle in session.angles_planned:
            if angle not in attempted:
                await self._enqueue(session, angle)
                return True
        return False

    async def _retry_failed(self, session: ResearchSession, runs: list[DotRun]) -> bool:
        for angle in session.angles_planned:
            attempts = [run for run in runs if run.topic == angle]
            if not attempts:
                continue
            last = attempts[-1]
            # A dot run fails for good reasons (Oracle network, bad content);
            # allow a bounded number of attempts before giving up on it.
            if (
                last.status == DotRunStatus.FAILED
                and len(attempts) < self._settings.research_max_attempts
            ):
                await self._enqueue(session, angle)
                return True
        return False

    async def _enqueue(self, session: ResearchSession, angle: str) -> None:
        try:
            run = await self._dots.enqueue(
                angle,
                session_id=str(session.research_session_id),
            )
        except DotEnqueueError as exc:
            log.warning(
                "research_enqueue_failed",
                extra={"session": str(session.research_session_id), "error": str(exc)},
            )
            session.status = ResearchSessionStatus.FAILED
            session.error = str(exc)[:4096]
            session.finished_at = utcnow()
            await self._repository.save_research_session(session)
            return
        log.info(
            "research_angle_enqueued",
            extra={
                "session": str(session.research_session_id),
                "run": str(run.dot_run_id),
                "angle": angle[:200],
            },
        )

    # --------------------------------------------------------------- finish
    @staticmethod
    def _completed_views(runs: list[DotRun]) -> list[dict[str, Any]]:
        return [
            {
                "topic": run.topic,
                "summary": run.summary,
                "dots_kept": run.dots_kept,
                "report_id": str(run.report_id) if run.report_id else None,
            }
            for run in runs
            if run.status == DotRunStatus.COMPLETED
        ]

    async def _finish(self, session: ResearchSession, runs: list[DotRun]) -> None:
        run_views = self._completed_views(runs)
        if not run_views:
            session.status = ResearchSessionStatus.FAILED
            session.error = "no research angle completed successfully"
            session.finished_at = utcnow()
            session.updated_at = utcnow()
            await self._repository.save_research_session(session)
            return

        synthesis = await self._researcher.synthesise_session(
            session.question,
            session.context,
            run_views,
            mode=session.mode.value,
        )
        evidence_ids: list[UUID] = []
        for run in runs:
            if run.status != DotRunStatus.COMPLETED or run.report_id is None:
                continue
            report = await self._repository.get_report(run.report_id)
            if report is not None:
                evidence_ids.extend(report.evidence_ids)

        sections: list[dict[str, Any]] = [
            {
                "key": "findings",
                "title": "Key findings",
                "items": synthesis.get("findings") or [],
            },
            {
                "key": "gaps",
                "title": "Open questions",
                "items": synthesis.get("gaps") or [],
            },
            {
                "key": "angles",
                "title": "Research angles",
                "items": run_views,
            },
        ]
        if synthesis.get("counterpoints"):
            sections.insert(
                1,
                {
                    "key": "counterpoints",
                    "title": "Counterpoints",
                    "items": synthesis["counterpoints"],
                },
            )

        report = Report(
            title=f"Research session — {session.question[:200]}",
            summary=synthesis.get("summary") or "",
            sections=sections,
            evidence_ids=evidence_ids,
            status=ReportStatus.FINAL,
            metadata={
                "source": "research",
                "research_session_id": str(session.research_session_id),
                "question": session.question,
                "mode": session.mode.value,
                "runs_completed": len(run_views),
            },
        )
        await self._repository.save_report(report)

        session.summary = synthesis.get("summary") or ""
        session.report_id = report.report_id
        session.runs_completed = len(run_views)
        session.status = ResearchSessionStatus.COMPLETED
        session.finished_at = utcnow()
        session.updated_at = utcnow()
        await self._repository.save_research_session(session)
        await self._publish(session, report)
        log.info(
            "research_session_completed",
            extra={
                "session": str(session.research_session_id),
                "question": session.question[:200],
                "angles": len(session.angles_planned),
                "runs": len(run_views),
            },
        )

    async def _publish(self, session: ResearchSession, report: Report) -> None:
        if self._publisher is None:
            return
        await self._publisher.publish(
            HermesEvent(
                type=EVENT_REPORT_PUBLISHED,
                severity="info",
                title=f"Research session complete: {session.question[:200]}",
                message=(session.summary or session.question)[:16384],
                metadata={
                    "research_session_id": str(session.research_session_id),
                    "report_id": str(report.report_id),
                    "question": session.question,
                    "mode": session.mode.value,
                    "angles": len(session.angles_planned),
                    "runs_completed": session.runs_completed,
                },
                tags=["argus", "research", "report"],
            )
        )
