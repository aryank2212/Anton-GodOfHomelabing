"""Tests for research sessions: repository, prompting and the coordinator."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest

from app.config.settings import Settings
from app.dots.research import ResearchCoordinator
from app.dots.researcher import DotResearcher
from app.models.dots import DotRun, DotRunStatus
from app.models.report import Report, ReportStatus
from app.models.research import (
    ResearchSession,
    ResearchSessionMode,
    ResearchSessionStatus,
)

# --------------------------------------------------------------------- oracle


class StubOracle:
    """Duck-typed OracleClient returning JSON matched by prompt keywords."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.asked: list[str] = []

    async def ask(self, message: str, *, context: str | None = None) -> str:
        self.asked.append(message)
        for key, payload in self._responses.items():
            if key in message:
                return json.dumps(payload)
        return "{}"


@pytest.mark.asyncio
async def test_plan_research_returns_angles_capped_to_max() -> None:
    oracle = StubOracle(
        {
            "Planner for a research": {
                "goal": "answer it",
                "research_angles": ["angle one", "angle two", "angle three", "angle four"],
            }
        }
    )
    plan = await DotResearcher(oracle).plan_research("some question", max_angles=3)
    assert plan["angles"] == ["angle one", "angle two", "angle three"]
    assert plan["counter_angles"] == []


@pytest.mark.asyncio
async def test_plan_research_falls_back_to_question() -> None:
    plan = await DotResearcher(StubOracle({})).plan_research("fallback question")
    assert plan["angles"] == ["fallback question"]


@pytest.mark.asyncio
async def test_plan_research_contradictory_returns_counter_angles() -> None:
    oracle = StubOracle(
        {
            "Planner for a research": {
                "goal": "answer it",
                "research_angles": ["establish facts"],
                "counter_angles": ["attack the answer", "alternative explanation"],
            }
        }
    )
    plan = await DotResearcher(oracle).plan_research(
        "some question", mode="contradictory", max_angles=3
    )
    assert plan["angles"] == ["establish facts"]
    assert plan["counter_angles"] == ["attack the answer", "alternative explanation"]


@pytest.mark.asyncio
async def test_plan_next_angle_returned_or_stop() -> None:
    researcher = DotResearcher(StubOracle({"PROGRESSIVE": {"next_angle": "dive deeper"}}))
    assert (
        await researcher.plan_next_angle("q", "c", [{"topic": "t", "summary": "s", "dots_kept": 1}])
        == "dive deeper"
    )
    stopped = DotResearcher(StubOracle({"PROGRESSIVE": {"next_angle": "STOP"}}))
    assert await stopped.plan_next_angle("q", "c", []) is None
    empty = DotResearcher(StubOracle({}))
    assert await empty.plan_next_angle("q", "c", []) is None


@pytest.mark.asyncio
async def test_session_prompts_never_exceed_oracle_cap() -> None:
    oracle = StubOracle({})
    researcher = DotResearcher(oracle)
    runs = [
        {"topic": f"angle {i}", "summary": "s" * 1200, "dots_kept": 5} for i in range(30)
    ]
    await researcher.plan_research("q" * 2000, context="c" * 4000, max_angles=6)
    await researcher.synthesise_session("q", "c", runs, mode="contradictory")
    await researcher.plan_next_angle("q" * 2000, "c" * 4000, runs)
    await researcher.assess_session("q" * 2000, "c" * 4000, runs, mode="progressive")
    await researcher.expected_gain("q" * 2000, "c" * 4000, runs, next_angles=["a"])
    for prompt in oracle.asked:
        assert len(prompt) <= 8000


@pytest.mark.asyncio
async def test_synthesise_session_parses_findings_gaps_and_counterpoints() -> None:
    oracle = StubOracle(
        {
            "research session": {
                "summary": "the answer",
                "findings": [
                    {"finding": "F", "support": "S", "angle": "angle one"},
                    "garbage entry",
                ],
                "counterpoints": ["the opposing view"],
                "gaps": ["gap one"],
            }
        }
    )
    result = await DotResearcher(oracle).synthesise_session("q", "c", [], mode="contradictory")
    assert result["summary"] == "the answer"
    assert result["findings"] == [{"finding": "F", "support": "S", "angle": "angle one"}]
    assert result["counterpoints"] == ["the opposing view"]
    assert result["gaps"] == ["gap one"]


# ----------------------------------------------------------------- repository


@pytest.mark.asyncio
async def test_research_session_repository_round_trip(repository) -> None:
    session = ResearchSession(
        question="what changed in the region?", context="background", max_angles=4
    )
    await repository.save_research_session(session)

    got = await repository.get_research_session(session.research_session_id)
    assert got is not None
    assert got.question == session.question
    assert got.status == ResearchSessionStatus.QUEUED

    sessions, total = await repository.list_research_sessions()
    assert total == 1
    assert sessions[0].research_session_id == session.research_session_id
    assert await repository.count_research_sessions(
        status=ResearchSessionStatus.QUEUED.value
    ) == 1

    got.status = ResearchSessionStatus.RUNNING
    await repository.save_research_session(got)
    refreshed = await repository.get_research_session(session.research_session_id)
    assert refreshed is not None and refreshed.status == ResearchSessionStatus.RUNNING


@pytest.mark.asyncio
async def test_oldest_active_session_and_stale_fail_sweep(repository) -> None:
    first = ResearchSession(question="first")
    second = ResearchSession(question="second")
    second.status = ResearchSessionStatus.RUNNING
    await repository.save_research_session(first)
    await repository.save_research_session(second)

    active = await repository.oldest_active_research_session(limit=1)
    assert active[0].research_session_id == first.research_session_id

    assert await repository.fail_stale_research_sessions() == 1
    refreshed = await repository.get_research_session(second.research_session_id)
    assert refreshed is not None
    assert refreshed.status == ResearchSessionStatus.FAILED
    assert refreshed.error == "interrupted by restart"
    assert await repository.fail_stale_research_sessions() == 0


@pytest.mark.asyncio
async def test_cancel_dot_runs_for_session(repository) -> None:
    session = ResearchSession(question="cancel me")
    await repository.save_research_session(session)
    queued = DotRun(topic="angle", session_id=session.research_session_id)
    await repository.save_dot_run(queued)
    done = DotRun(topic="old angle", session_id=session.research_session_id)
    done.status = DotRunStatus.COMPLETED
    await repository.save_dot_run(done)

    assert (
        await repository.cancel_dot_runs_for_session(session.research_session_id) == 1
    )
    refreshed = await repository.get_dot_run(queued.dot_run_id)
    assert refreshed is not None and refreshed.status == DotRunStatus.CANCELLED
    untouched = await repository.get_dot_run(done.dot_run_id)
    assert untouched is not None and untouched.status == DotRunStatus.COMPLETED


# ---------------------------------------------------------------- coordinator


class FakeDots:
    """Stands in for DotsEngine: persists real runs linked to the session."""

    def __init__(self, repository) -> None:
        self._repository = repository
        self.enqueued: list[str] = []

    async def enqueue(self, topic: str, *, session_id: str | None = None, **_: Any) -> DotRun:
        run = DotRun(topic=topic, session_id=UUID(session_id) if session_id else None)
        await self._repository.save_dot_run(run)
        self.enqueued.append(topic)
        return run


class FakeSessionResearcher:
    def __init__(
        self,
        angles: list[str],
        synthesis: dict[str, Any],
        *,
        counter_angles: list[str] | None = None,
        next_angles: list[str] | None = None,
        assessments: list[dict[str, Any]] | None = None,
        gain: float = 1.0,
    ) -> None:
        self._angles = angles
        self._counter_angles = counter_angles or []
        self._next_angles = list(next_angles or [])
        self._assessments = list(assessments or [])
        self._gain = gain
        self._synthesis = synthesis
        self.synthesise_calls: list[dict[str, Any]] = []
        self.assess_calls: list[dict[str, Any]] = []
        self.gain_calls: list[list[str]] = []

    async def plan_research(
        self, question: str, context: str = "", *, mode: str = "single_pass", max_angles: int = 3
    ) -> dict[str, list[str]]:
        return {
            "angles": self._angles[:max_angles],
            "counter_angles": self._counter_angles[:max_angles],
        }

    async def plan_next_angle(
        self, question: str, context: str, runs: list[dict[str, Any]]
    ) -> str | None:
        if self._next_angles:
            return self._next_angles.pop(0)
        return None

    async def assess_session(
        self,
        question: str,
        context: str,
        runs: list[dict[str, Any]],
        *,
        mode: str = "single_pass",
    ) -> dict[str, Any]:
        self.assess_calls.append({"mode": mode, "runs": runs})
        if self._assessments:
            return self._assessments.pop(0)
        return {"complete": True, "assessment": "", "next_angles": []}

    async def expected_gain(
        self,
        question: str,
        context: str,
        runs: list[dict[str, Any]],
        *,
        mode: str = "single_pass",
        next_angles: list[str],
    ) -> float:
        self.gain_calls.append(next_angles)
        return self._gain

    async def synthesise_session(
        self, question: str, context: str, runs: list[dict[str, Any]], *, mode: str = "single_pass"
    ) -> dict[str, Any]:
        self.synthesise_calls.append({"mode": mode, "runs": runs})
        return dict(self._synthesis)


def _coordinator(
    repository,
    *,
    dots=None,
    researcher=None,
    publisher=None,
    max_rounds: int | None = None,
    min_gain: float | None = None,
) -> ResearchCoordinator:
    fallback_researcher = FakeSessionResearcher(
        ["angle one"], {"summary": "s", "findings": [], "gaps": []}
    )
    settings: dict[str, Any] = {"research_max_attempts": 2}
    if max_rounds is not None:
        settings["research_max_rounds"] = max_rounds
    if min_gain is not None:
        settings["research_min_information_gain"] = min_gain
    return ResearchCoordinator(
        settings=Settings(**settings),
        repository=repository,
        oracle=None,
        researcher=researcher or fallback_researcher,
        dots=dots or FakeDots(repository),
        publisher=publisher,
    )


async def _latest_runs(repository, session: ResearchSession) -> list[DotRun]:
    return await repository.list_dot_runs_for_session(session.research_session_id)


@pytest.mark.asyncio
async def test_coordinator_runs_session_to_completion(repository) -> None:
    session = ResearchSession(question="answer this", max_angles=2)
    await repository.save_research_session(session)
    coordinator = _coordinator(
        repository,
        researcher=FakeSessionResearcher(
            ["angle one", "angle two"],
            {"summary": "the answer", "findings": [{"finding": "F"}], "gaps": ["g"]},
        ),
    )

    await coordinator.tick()  # plan + enqueue angle one
    await coordinator.tick()  # nothing new while angle one is queued
    refreshed = await repository.get_research_session(session.research_session_id)
    assert refreshed is not None
    assert refreshed.status == ResearchSessionStatus.RUNNING
    assert refreshed.angles_planned == ["angle one", "angle two"]
    runs = await _latest_runs(repository, session)
    assert len(runs) == 1 and runs[0].topic == "angle one"
    assert runs[0].session_id == session.research_session_id

    # angle one completes with its own report carrying two evidence ids.
    report = Report(
        title="angle one report",
        status=ReportStatus.FINAL,
        evidence_ids=[UUID(int=1), UUID(int=2)],
    )
    await repository.save_report(report)
    runs[0].status = DotRunStatus.COMPLETED
    runs[0].summary = "found things"
    runs[0].report_id = report.report_id
    await repository.save_dot_run(runs[0])

    await coordinator.tick()  # angle two now enqueued
    runs = await _latest_runs(repository, session)
    assert [run.topic for run in runs] == ["angle one", "angle two"]

    runs[1].status = DotRunStatus.COMPLETED
    runs[1].summary = "more things"
    await repository.save_dot_run(runs[1])

    await coordinator.tick()  # all angles done -> synthesis
    finished = await repository.get_research_session(session.research_session_id)
    assert finished is not None
    assert finished.status == ResearchSessionStatus.COMPLETED
    assert finished.summary == "the answer"
    assert finished.runs_completed == 2
    assert finished.report_id is not None
    assert finished.finished_at is not None

    final_report = await repository.get_report(finished.report_id)
    assert final_report is not None
    # evidence unioned from the completed angle reports.
    assert set(final_report.evidence_ids) == {UUID(int=1), UUID(int=2)}
    assert final_report.sections[1]["key"] == "gaps"

    # nothing left to do after completion.
    await coordinator.tick()
    assert len(await _latest_runs(repository, session)) == 2


@pytest.mark.asyncio
async def test_coordinator_synthesises_partial_when_an_angle_never_completed(
    repository,
) -> None:
    """A failed angle does not sink the session: completed angles still count."""
    session = ResearchSession(question="two-angle session", max_angles=2)
    await repository.save_research_session(session)
    coordinator = _coordinator(
        repository,
        researcher=FakeSessionResearcher(
            ["good angle", "bad angle"], {"summary": "partial", "findings": [], "gaps": []}
        ),
    )

    await coordinator.tick()  # enqueue good angle
    runs = await _latest_runs(repository, session)
    runs[0].status = DotRunStatus.COMPLETED
    runs[0].summary = "good"
    await repository.save_dot_run(runs[0])

    await coordinator.tick()  # enqueue bad angle
    runs = await _latest_runs(repository, session)
    assert [run.topic for run in runs] == ["good angle", "bad angle"]
    runs[1].status = DotRunStatus.FAILED
    await repository.save_dot_run(runs[1])

    await coordinator.tick()  # retry bad angle (attempt 2)
    runs = await _latest_runs(repository, session)
    assert len(runs) == 3
    runs[2].status = DotRunStatus.FAILED
    await repository.save_dot_run(runs[2])

    await coordinator.tick()  # attempts exhausted -> partial synthesis
    finished = await repository.get_research_session(session.research_session_id)
    assert finished is not None
    assert finished.status == ResearchSessionStatus.COMPLETED
    assert finished.runs_completed == 1  # only the successful angle counts
    assert finished.summary == "partial"
    assert len(await _latest_runs(repository, session)) == 3  # no more retries


@pytest.mark.asyncio
async def test_coordinator_fails_session_when_nothing_completes(repository) -> None:
    session = ResearchSession(question="doomed", max_angles=1)
    await repository.save_research_session(session)
    coordinator = _coordinator(repository, researcher=FakeSessionResearcher(["only angle"], {
        "summary": "", "findings": [], "gaps": [],
    }))

    await coordinator.tick()
    runs = await _latest_runs(repository, session)
    runs[0].status = DotRunStatus.FAILED
    await repository.save_dot_run(runs[0])
    await coordinator.tick()
    runs = await _latest_runs(repository, session)
    runs[1].status = DotRunStatus.FAILED
    await repository.save_dot_run(runs[1])

    # make the failed session finish: exhausted attempts, zero completed.
    finished = await repository.get_research_session(session.research_session_id)
    assert finished is None or finished.status == ResearchSessionStatus.RUNNING
    await coordinator.tick()
    finished = await repository.get_research_session(session.research_session_id)
    assert finished is not None
    assert finished.status == ResearchSessionStatus.FAILED
    assert "no research angle completed" in finished.error


@pytest.mark.asyncio
async def test_coordinator_publishes_completion_event(repository) -> None:
    from app.models.event import HermesEvent

    class RecordingPublisher:
        def __init__(self) -> None:
            self.published: list[HermesEvent] = []

        async def publish(self, event: HermesEvent) -> None:
            self.published.append(event)

    publisher = RecordingPublisher()
    session = ResearchSession(question="publish me", max_angles=1)
    await repository.save_research_session(session)
    coordinator = _coordinator(
        repository,
        publisher=publisher,
        researcher=FakeSessionResearcher(
            ["angle one"], {"summary": "done", "findings": [], "gaps": []}
        ),
    )

    await coordinator.tick()
    runs = await _latest_runs(repository, session)
    runs[0].status = DotRunStatus.COMPLETED
    runs[0].summary = "done thing"
    await repository.save_dot_run(runs[0])
    await coordinator.tick()

    events = [e for e in publisher.published if e.type == "report_published"]
    assert len(events) == 1
    assert events[0].metadata["research_session_id"] == str(session.research_session_id)
    assert events[0].metadata["runs_completed"] == 1
    assert "research" in events[0].tags


@pytest.mark.asyncio
async def test_progressive_replans_next_angle_from_findings(repository) -> None:
    """Progressive sessions start with one angle and re-plan as findings land."""
    session = ResearchSession(
        question="build on prior",
        mode=ResearchSessionMode.PROGRESSIVE,
        max_angles=3,
    )
    await repository.save_research_session(session)
    researcher = FakeSessionResearcher(
        ["first angle", "leftover (unused)"],
        {"summary": "progressive answer", "findings": [], "gaps": []},
        next_angles=["second angle"],
    )
    coordinator = _coordinator(repository, researcher=researcher)

    await coordinator.tick()  # plan + enqueue the foundation angle only
    refreshed = await repository.get_research_session(session.research_session_id)
    assert refreshed is not None
    assert refreshed.angles_planned == ["first angle"]

    runs = await _latest_runs(repository, session)
    runs[0].status = DotRunStatus.COMPLETED
    runs[0].summary = "found the seeds"
    await repository.save_dot_run(runs[0])

    await coordinator.tick()  # re-plan the next angle from the first findings
    refreshed = await repository.get_research_session(session.research_session_id)
    assert refreshed is not None
    assert refreshed.angles_planned == ["first angle", "second angle"]
    runs = await _latest_runs(repository, session)
    assert [run.topic for run in runs] == ["first angle", "second angle"]

    runs[1].status = DotRunStatus.COMPLETED
    runs[1].summary = "went deeper"
    await repository.save_dot_run(runs[1])

    await coordinator.tick()  # max_angles reached -> finish
    finished = await repository.get_research_session(session.research_session_id)
    assert finished is not None
    assert finished.status == ResearchSessionStatus.COMPLETED
    assert finished.summary == "progressive answer"
    assert finished.runs_completed == 2
    assert researcher.synthesise_calls[-1]["mode"] == "progressive"
    # re-planning was seeded with the completed run's findings.
    seeds = researcher.synthesise_calls[-1]["runs"]
    assert any(view["summary"] == "found the seeds" for view in seeds)
    assert any(view["summary"] == "went deeper" for view in seeds)


@pytest.mark.asyncio
async def test_progressive_stops_when_oracle_calls_it_done(repository) -> None:
    """A STOP from the planner finishes the session early, no spare angles."""
    session = ResearchSession(
        question="enough is enough",
        mode=ResearchSessionMode.PROGRESSIVE,
        max_angles=5,
    )
    await repository.save_research_session(session)
    coordinator = _coordinator(
        repository,
        researcher=FakeSessionResearcher(
            ["only angle"], {"summary": "done", "findings": [], "gaps": []}
        ),
    )

    await coordinator.tick()
    runs = await _latest_runs(repository, session)
    runs[0].status = DotRunStatus.COMPLETED
    runs[0].summary = "everything found"
    await repository.save_dot_run(runs[0])

    await coordinator.tick()  # planner returns no next angle -> finish
    finished = await repository.get_research_session(session.research_session_id)
    assert finished is not None
    assert finished.status == ResearchSessionStatus.COMPLETED
    assert finished.runs_completed == 1
    assert len(await _latest_runs(repository, session)) == 1


@pytest.mark.asyncio
async def test_contradictory_runs_angles_and_counter_angles(repository) -> None:
    """Counter-angles run alongside the main angles and weigh into the report."""
    session = ResearchSession(
        question="which side wins",
        mode=ResearchSessionMode.CONTRADICTORY,
        max_angles=3,
    )
    await repository.save_research_session(session)
    researcher = FakeSessionResearcher(
        ["establish case"],
        {"summary": "weighed", "findings": [{"finding": "F"}], "counterpoints": ["attack"]},
        counter_angles=["attack the case"],
    )
    coordinator = _coordinator(repository, researcher=researcher)

    await coordinator.tick()
    refreshed = await repository.get_research_session(session.research_session_id)
    assert refreshed is not None
    assert refreshed.angles_planned == ["establish case", "attack the case"]
    assert refreshed.metadata["counter_angles"] == ["attack the case"]

    runs = await _latest_runs(repository, session)
    assert len(runs) == 1 and runs[0].topic == "establish case"

    runs[0].status = DotRunStatus.COMPLETED
    runs[0].summary = "case supported"
    await repository.save_dot_run(runs[0])

    await coordinator.tick()  # counter-angle now enqueued
    runs = await _latest_runs(repository, session)
    assert [run.topic for run in runs] == ["establish case", "attack the case"]
    runs[1].status = DotRunStatus.COMPLETED
    runs[1].summary = "attempt failed to refute"
    await repository.save_dot_run(runs[1])

    await coordinator.tick()  # all angles done -> weighted synthesis
    finished = await repository.get_research_session(session.research_session_id)
    assert finished is not None
    assert finished.status == ResearchSessionStatus.COMPLETED
    assert finished.runs_completed == 2
    assert researcher.synthesise_calls[-1]["mode"] == "contradictory"

    final_report = await repository.get_report(finished.report_id)
    assert final_report is not None
    keys = [section["key"] for section in final_report.sections]
    assert keys == ["findings", "counterpoints", "gaps", "angles"]
    assert final_report.metadata["mode"] == "contradictory"


# --------------------------------------------------------- adaptive loop


@pytest.mark.asyncio
async def test_adaptive_loop_runs_rounds_until_oracle_says_complete(repository) -> None:
    """An incomplete verdict plans another round; rounds stop on complete."""
    session = ResearchSession(question="loop until done", max_angles=1)
    await repository.save_research_session(session)
    researcher = FakeSessionResearcher(
        ["first"],
        {"summary": "looped", "findings": [], "gaps": []},
        assessments=[
            {"complete": False, "assessment": "need depth", "next_angles": ["second"]},
            {"complete": False, "assessment": "still open", "next_angles": ["third"]},
            {"complete": True, "assessment": "enough", "next_angles": []},
        ],
    )
    coordinator = _coordinator(repository, researcher=researcher, max_rounds=3)

    await coordinator.tick()  # round 1: first angle
    runs = await _latest_runs(repository, session)
    runs[0].status = DotRunStatus.COMPLETED
    runs[0].summary = "round one"
    await repository.save_dot_run(runs[0])
    await coordinator.tick()  # assess -> not complete -> second angle
    assert len(researcher.assess_calls) == 1

    runs = await _latest_runs(repository, session)
    assert [run.topic for run in runs] == ["first", "second"]
    runs[1].status = DotRunStatus.COMPLETED
    runs[1].summary = "round two"
    await repository.save_dot_run(runs[1])
    await coordinator.tick()  # assess again -> third angle
    assert len(researcher.assess_calls) == 2

    runs = await _latest_runs(repository, session)
    assert [run.topic for run in runs] == ["first", "second", "third"]
    runs[2].status = DotRunStatus.COMPLETED
    runs[2].summary = "round three"
    await repository.save_dot_run(runs[2])

    await coordinator.tick()  # rounds budget spent -> finish
    finished = await repository.get_research_session(session.research_session_id)
    assert finished is not None
    assert finished.status == ResearchSessionStatus.COMPLETED
    assert finished.angles_planned == ["first", "second", "third"]
    assert finished.metadata["rounds"] == 2
    assert finished.runs_completed == 3
    assert len(researcher.assess_calls) == 2  # third verdict never consulted


@pytest.mark.asyncio
async def test_adaptive_loop_respects_max_rounds(repository) -> None:
    """Rounds are capped even when Oracle keeps asking for more."""
    session = ResearchSession(question="capped rounds", max_angles=1)
    await repository.save_research_session(session)
    researcher = FakeSessionResearcher(
        ["first"],
        {"summary": "capped", "findings": [], "gaps": []},
        assessments=[
            {"complete": False, "assessment": "more", "next_angles": ["second"]},
            {"complete": False, "assessment": "still more", "next_angles": ["third"]},
        ],
    )
    coordinator = _coordinator(repository, researcher=researcher, max_rounds=2)

    await coordinator.tick()
    runs = await _latest_runs(repository, session)
    runs[0].status = DotRunStatus.COMPLETED
    runs[0].summary = "one"
    await repository.save_dot_run(runs[0])

    await coordinator.tick()  # one adaptive round allowed
    runs = await _latest_runs(repository, session)
    assert [run.topic for run in runs] == ["first", "second"]
    runs[1].status = DotRunStatus.COMPLETED
    runs[1].summary = "two"
    await repository.save_dot_run(runs[1])

    await coordinator.tick()  # rounds exhausted -> finish despite not complete
    finished = await repository.get_research_session(session.research_session_id)
    assert finished is not None
    assert finished.status == ResearchSessionStatus.COMPLETED
    assert finished.runs_completed == 2
    assert len(researcher.assess_calls) == 1
    assert len(await _latest_runs(repository, session)) == 2


@pytest.mark.asyncio
async def test_adaptive_loop_discards_repeated_angles(repository) -> None:
    """Oracle repeating an existing angle cannot loop the session forever."""
    session = ResearchSession(question="no repeats", max_angles=1)
    await repository.save_research_session(session)
    researcher = FakeSessionResearcher(
        ["first"],
        {"summary": "final", "findings": [], "gaps": []},
        assessments=[{"complete": False, "assessment": "again", "next_angles": ["first"]}],
    )
    coordinator = _coordinator(repository, researcher=researcher)

    await coordinator.tick()
    runs = await _latest_runs(repository, session)
    runs[0].status = DotRunStatus.COMPLETED
    runs[0].summary = "one"
    await repository.save_dot_run(runs[0])

    await coordinator.tick()  # duplicate angle filtered -> finish
    finished = await repository.get_research_session(session.research_session_id)
    assert finished is not None
    assert finished.status == ResearchSessionStatus.COMPLETED
    assert finished.angles_planned == ["first"]
    assert finished.runs_completed == 1
    assert len(await _latest_runs(repository, session)) == 1


# ------------------------------------------------ information-gain stopping


@pytest.mark.asyncio
async def test_information_gain_stops_a_low_value_round(repository) -> None:
    """A round Oracle rates < threshold ends the session; no further runs."""
    session = ResearchSession(question="diminishing returns", max_angles=1)
    await repository.save_research_session(session)
    researcher = FakeSessionResearcher(
        ["first"],
        {"summary": "early stop", "findings": [], "gaps": []},
        assessments=[{"complete": False, "assessment": "more", "next_angles": ["second"]}],
        gain=0.1,
    )
    coordinator = _coordinator(repository, researcher=researcher, min_gain=0.5)

    await coordinator.tick()
    runs = await _latest_runs(repository, session)
    runs[0].status = DotRunStatus.COMPLETED
    runs[0].summary = "covered it"
    await repository.save_dot_run(runs[0])

    await coordinator.tick()  # assess wants more, but gain too low -> finish
    finished = await repository.get_research_session(session.research_session_id)
    assert finished is not None
    assert finished.status == ResearchSessionStatus.COMPLETED
    assert finished.runs_completed == 1
    assert finished.metadata["info_gain"] == 0.1
    assert finished.metadata["stopped_low_information_gain"] is True
    assert len(await _latest_runs(repository, session)) == 1
    assert researcher.gain_calls == [["second"]]


@pytest.mark.asyncio
async def test_information_gain_allows_a_high_value_round(repository) -> None:
    """A round rated at or above the threshold runs as normal."""
    session = ResearchSession(question="keep going", max_angles=1)
    await repository.save_research_session(session)
    researcher = FakeSessionResearcher(
        ["first"],
        {"summary": "finished", "findings": [], "gaps": []},
        assessments=[{"complete": False, "assessment": "more", "next_angles": ["second"]}],
        gain=0.9,
    )
    coordinator = _coordinator(repository, researcher=researcher, min_gain=0.5)

    await coordinator.tick()
    runs = await _latest_runs(repository, session)
    runs[0].status = DotRunStatus.COMPLETED
    runs[0].summary = "one"
    await repository.save_dot_run(runs[0])

    await coordinator.tick()  # gain high enough -> second angle enqueued
    runs = await _latest_runs(repository, session)
    assert [run.topic for run in runs] == ["first", "second"]
    assert researcher.gain_calls == [["second"]]
    refreshed = await repository.get_research_session(session.research_session_id)
    assert refreshed is not None and refreshed.metadata["info_gain"] == 0.9
