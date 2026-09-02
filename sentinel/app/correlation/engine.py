"""Correlation engine — combines observations into situations.

The engine keeps a bounded, time-bounded window of recent observations and
evaluates every enabled rule against it. Each rule transitions through a
small lifecycle:

    inactive -> (match persists for ``stable_for``) -> active -> resolved

Resolved situations can only re-activate after ``cooldown`` seconds. On boot
the engine loads recent observations and active situations from the database
so state survives restarts; a short grace period stops absence rules from
firing before observers have produced data.

Adding a correlation rule is a YAML edit. Adding a rule *type* means adding
an evaluator here (or subclassing and overriding ``_evaluate``).
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

from pydantic import BaseModel

from app.core.logging import get_logger
from app.correlation.rules import Condition, Rule
from app.models.observation import Observation, utcnow
from app.models.situation import Situation

log = get_logger(__name__)


class SituationChange(BaseModel):
    """A state transition of a rule's situation."""

    action: str  # activated | updated | resolved
    rule_id: str
    publish: bool = True
    situation: Situation


class _RuleState:
    """In-memory lifecycle state for one rule."""

    __slots__ = ("condition_since", "situation", "cooldown_until")

    def __init__(self) -> None:
        self.condition_since: datetime | None = None
        self.situation: Situation | None = None
        self.cooldown_until: datetime | None = None


class CorrelationEngine:
    def __init__(
        self,
        *,
        rules: list[Rule],
        window_size: int = 10_000,
        grace_seconds: float = 60.0,
    ) -> None:
        self.rules = [rule for rule in rules if rule.enabled]
        self.window_size = window_size
        self.grace_seconds = grace_seconds
        self._window: deque[Observation] = deque(maxlen=window_size)
        self._state: dict[str, _RuleState] = {rule.id: _RuleState() for rule in self.rules}
        self._started = utcnow()

    # ------------------------------------------------------------------ setup
    @property
    def grace_until(self) -> datetime:
        return self._started + timedelta(seconds=self.grace_seconds)

    @property
    def window(self) -> list[Observation]:
        return list(self._window)

    async def load_recent(self, repository) -> None:
        """Warm the window and restore active situations from storage."""
        observations = await repository.recent_observations(days=1, limit=self.window_size)
        for observation in observations:
            self._window.append(observation)
        active = await repository.active_situations()
        for situation in active:
            state = self._state.get(situation.rule_id)
            if state is not None:
                state.situation = situation
                state.condition_since = utcnow()
        log.info(
            "correlation_warmed",
            extra={"observations": len(observations), "active_situations": len(active)},
        )

    # ------------------------------------------------------------------ feed
    async def feed(self, observation: Observation) -> list[SituationChange]:
        """Ingest one observation and re-evaluate affected rules."""
        self._window.append(observation)
        return await self.evaluate()

    async def evaluate(self) -> list[SituationChange]:
        """Re-evaluate every rule against the current window."""
        changes: list[SituationChange] = []
        for rule in self.rules:
            change = self._evaluate_rule(rule)
            if change is not None:
                changes.append(change)
        return changes

    # ------------------------------------------------------------- rule eval
    def _evaluate_rule(self, rule: Rule) -> SituationChange | None:
        now = utcnow()
        state = self._state[rule.id]

        matched, matched_observations = self._match(rule, now)

        if state.situation is not None and state.situation.active:
            if matched:
                return self._refresh(rule, state, matched_observations, now)
            if now < self.grace_until:
                return None
            resolved = state.situation.resolve(
                at=now, summary=f"Rule '{rule.name}' no longer matches."
            )
            state.situation = resolved
            state.condition_since = None
            state.cooldown_until = now + timedelta(seconds=rule.cooldown)
            log.info(
                "situation_resolved",
                extra={"situation_id": str(resolved.situation_id), "rule_id": rule.id},
            )
            return SituationChange(
                action="resolved", rule_id=rule.id, publish=rule.publish, situation=resolved
            )

        if not matched:
            state.condition_since = None
            return None
        if state.cooldown_until is not None and now < state.cooldown_until:
            return None

        if state.condition_since is None:
            state.condition_since = now
        held = (now - state.condition_since).total_seconds()
        if held < rule.stable_for:
            return None

        confidence = self._confidence(rule, matched_observations)
        if confidence < rule.confidence.min:
            return None

        situation = self._build_situation(rule, matched_observations, confidence, now)
        state.situation = situation
        state.condition_since = now
        log.info(
            "situation_activated",
            extra={
                "situation_id": str(situation.situation_id),
                "rule_id": rule.id,
                "confidence": confidence,
            },
        )
        return SituationChange(
            action="activated", rule_id=rule.id, publish=rule.publish, situation=situation
        )

    def _refresh(
        self,
        rule: Rule,
        state: _RuleState,
        matched_observations: list[Observation],
        now: datetime,
    ) -> SituationChange | None:
        situation = state.situation
        assert situation is not None
        derived = [obs.observation_id for obs in matched_observations[-20:]]
        sources = sorted({obs.source for obs in matched_observations})
        confidence = self._confidence(rule, matched_observations)
        if (
            sorted(str(i) for i in derived) == sorted(str(i) for i in situation.derived_from)
            and abs(confidence - situation.confidence) < 0.01
        ):
            return None
        updated = situation.model_copy(
            update={
                "confidence": confidence,
                "derived_from": derived,
                "sources": sources,
                "updated_at": now,
            }
        )
        state.situation = updated
        return SituationChange(
            action="updated", rule_id=rule.id, publish=rule.publish, situation=updated
        )

    # -------------------------------------------------------------- matching
    def _match(self, rule: Rule, now: datetime) -> tuple[bool, list[Observation]]:
        if rule.type == "absence":
            return self._match_absence(rule, now)
        if rule.type == "count":
            return self._match_count(rule, now)
        return self._match_boolean(rule, now)

    def _condition_matches(
        self, condition: Condition, observation: Observation, now: datetime
    ) -> bool:
        if condition.sources and observation.source not in condition.sources:
            return False
        if condition.objects and observation.object not in condition.objects:
            return False
        if condition.states and observation.state not in condition.states:
            return False
        if condition.tags and not any(tag in observation.tags for tag in condition.tags):
            return False
        age = (now - observation.timestamp).total_seconds()
        return age <= condition.window_seconds

    def _matches_any(self, condition: Condition, now: datetime) -> bool:
        return any(self._condition_matches(condition, obs, now) for obs in self._window)

    def _match_boolean(self, rule: Rule, now: datetime) -> tuple[bool, list[Observation]]:
        match = rule.match
        all_satisfied = all(self._matches_any(cond, now) for cond in match.all)
        any_satisfied = not match.any or any(self._matches_any(cond, now) for cond in match.any)
        not_satisfied = not any(self._matches_any(cond, now) for cond in match.not_)
        if not (all_satisfied and any_satisfied and not_satisfied):
            return False, []
        supporting = []
        seen: set[object] = set()
        for obs in self._window:
            if obs.observation_id not in seen and any(
                self._condition_matches(cond, obs, now) for cond in [*match.all, *match.any]
            ):
                seen.add(obs.observation_id)
                supporting.append(obs)
        return True, supporting

    def _match_absence(self, rule: Rule, now: datetime) -> tuple[bool, list[Observation]]:
        if now < self.grace_until:
            return False, []
        cutoff = max(rule.absent_for, 1.0)
        for condition in rule.match.all:
            if any(
                self._condition_matches(condition, obs, now)
                and (now - obs.timestamp).total_seconds() < cutoff
                for obs in self._window
            ):
                return False, []
        return True, []

    def _match_count(self, rule: Rule, now: datetime) -> tuple[bool, list[Observation]]:
        cutoff = now - timedelta(seconds=rule.window_seconds)
        matching = [
            obs
            for obs in self._window
            if obs.timestamp >= cutoff
            and any(self._condition_matches(cond, obs, now) for cond in rule.match.all)
        ]
        return len(matching) >= rule.threshold, matching[-50:]

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _confidence(rule: Rule, matched_observations: list[Observation]) -> float:
        base = rule.confidence.base
        if matched_observations:
            average = sum(obs.confidence for obs in matched_observations) / len(
                matched_observations
            )
            base *= average
        return round(min(1.0, max(0.0, base)), 3)

    @staticmethod
    def _build_situation(
        rule: Rule,
        matched_observations: list[Observation],
        confidence: float,
        now: datetime,
    ) -> Situation:
        return Situation(
            rule_id=rule.id,
            type=rule.id,
            name=rule.name,
            severity=rule.severity,
            confidence=confidence,
            summary=(
                f"Rule '{rule.name}' matched {len(matched_observations)} supporting observation(s)."
            ),
            derived_from=[obs.observation_id for obs in matched_observations[-20:]],
            sources=sorted({obs.source for obs in matched_observations}),
            metadata={"description": rule.description, "category": rule.category.value},
            created_at=now,
            updated_at=now,
        )
