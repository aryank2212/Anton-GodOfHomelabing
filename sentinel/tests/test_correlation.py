"""Correlation engine behaviour: boolean, absence, count, lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.correlation.engine import CorrelationEngine
from app.correlation.rules import Rule
from app.models.observation import Category, Observation
from app.models.situation import SituationStatus
from tests.conftest import observation


class Clock:
    """Deterministic clock for the engine (patching app.correlation.engine.utcnow)."""

    def __init__(self) -> None:
        self.now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch):
    instance = Clock()
    monkeypatch.setattr("app.correlation.engine.utcnow", instance)
    return instance


def make_rule(**overrides) -> Rule:
    base = {
        "id": "test_rule",
        "name": "Test Rule",
        "type": "boolean",
        "category": "network",
        "match": {"all": [{"source": "router", "object": "gateway", "state": "offline"}]},
        "stable_for": 0,
        "cooldown": 0,
    }
    base.update(overrides)
    return Rule.model_validate(base)


def make_engine(rules: list[Rule], grace: float = 0.0) -> CorrelationEngine:
    return CorrelationEngine(rules=rules, window_size=1000, grace_seconds=grace)


def obs(clock: Clock, **overrides) -> Observation:
    defaults = dict(
        source="router",
        category=Category.NETWORK,
        object="gateway",
        state="offline",
        timestamp=clock.now,
    )
    defaults.update(overrides)
    return Observation(**defaults)


async def test_boolean_rule_activates(clock):
    engine = make_engine([make_rule()])
    changes = await engine.feed(obs(clock))
    assert len(changes) == 1
    change = changes[0]
    assert change.action == "activated"
    assert change.publish is True
    situation = change.situation
    assert situation.rule_id == "test_rule"
    assert situation.type == "test_rule"
    assert situation.sources == ["router"]
    assert situation.status == SituationStatus.ACTIVE


async def test_boolean_rule_requires_stable_for(clock):
    engine = make_engine([make_rule(stable_for=10)])
    assert await engine.feed(obs(clock)) == []
    clock.advance(5)
    assert await engine.evaluate() == []
    clock.advance(5)
    changes = await engine.evaluate()
    assert len(changes) == 1
    assert changes[0].action == "activated"


async def test_situation_resolves_when_match_goes_stale(clock):
    engine = make_engine([make_rule(stable_for=0)])
    await engine.feed(obs(clock))
    clock.advance(301)  # beyond the 300s observation window
    changes = await engine.evaluate()
    assert len(changes) == 1
    assert changes[0].action == "resolved"
    assert not changes[0].situation.active


async def test_cooldown_blocks_reactivation(clock):
    rule = make_rule(stable_for=0, cooldown=100)
    engine = make_engine([rule])
    await engine.feed(obs(clock))
    clock.advance(301)
    await engine.evaluate()  # resolved
    await engine.feed(obs(clock))  # offline again
    assert await engine.evaluate() == []  # cooling down
    clock.advance(101)
    changes = await engine.evaluate()
    assert len(changes) == 1
    assert changes[0].action == "activated"


async def test_confidence_minimum_gates_activation(clock):
    engine = make_engine([make_rule(confidence={"base": 0.8, "min": 0.95})])
    assert await engine.feed(obs(clock, confidence=0.5)) == []


async def test_confidence_from_observations(clock):
    engine = make_engine([make_rule(confidence={"base": 0.8, "min": 0.0})])
    changes = await engine.feed(obs(clock, confidence=0.9))
    assert changes[0].situation.confidence == round(0.8 * 0.9, 3)


async def test_publish_false_suppresses_publish_flag(clock):
    engine = make_engine([make_rule(publish=False)])
    changes = await engine.feed(obs(clock))
    assert changes[0].publish is False


async def test_absence_rule(clock):
    rule = make_rule(
        type="absence",
        absent_for=10,
        match={"all": [{"source": "watcher", "object": "watcher", "state": "online"}]},
    )
    engine = make_engine([rule])
    # No healthy signal ever seen -> activates
    changes = await engine.evaluate()
    assert len(changes) == 1
    assert changes[0].action == "activated"
    # Healthy signal resumes -> resolves (feed re-evaluates immediately)
    changes = await engine.feed(obs(clock, source="watcher", object="watcher", state="online"))
    assert [change.action for change in changes] == ["resolved"]
    # Signal vanishes for absent_for -> re-activates
    clock.advance(11)
    changes = await engine.evaluate()
    assert len(changes) == 1
    assert changes[0].action == "activated"


async def test_absence_respects_grace_period(clock):
    rule = make_rule(
        type="absence",
        absent_for=10,
        match={"all": [{"source": "watcher", "object": "watcher", "state": "online"}]},
    )
    engine = make_engine([rule], grace=60)
    assert await engine.evaluate() == []
    clock.advance(61)
    changes = await engine.evaluate()
    assert len(changes) == 1


async def test_count_rule_threshold(clock):
    rule = make_rule(
        type="count",
        threshold=3,
        match={"all": [{"source": "system", "object": "cpu", "state": "high"}]},
    )
    engine = make_engine([rule])
    for _ in range(2):
        await engine.feed(obs(clock, source="system", object="cpu", state="high"))
    assert await engine.evaluate() == []
    changes = await engine.feed(obs(clock, source="system", object="cpu", state="high"))
    assert len(changes) == 1
    assert changes[0].action == "activated"


async def test_match_any_and_not(clock):
    rule = make_rule(match={"any": [{"state": "a"}, {"state": "b"}], "not": [{"state": "blocked"}]})
    engine = make_engine([rule])
    await engine.feed(obs(clock, state="blocked"))
    assert await engine.evaluate() == []
    clock.advance(301)  # blocked observation ages out of the window
    changes = await engine.feed(obs(clock, state="a"))
    assert len(changes) == 1


async def test_load_recent_warms_window(repository):
    from app.correlation.loader import load_rules

    await repository.add_observation(
        observation(source="router", object="gateway", state="offline")
    )
    rules = load_rules("app/config/rules.yaml")
    engine = CorrelationEngine(rules=rules, window_size=1000, grace_seconds=0)
    await engine.load_recent(repository)
    assert len(engine.window) == 1
    matched, _ = engine._match_boolean(rules[0], datetime.now(UTC))
    assert matched
