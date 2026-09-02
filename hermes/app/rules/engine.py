from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any

from app.rules.models import Remediation, Rule, RuleAction, RulesFile


@dataclass(frozen=True)
class RuleDecision:
    action: RuleAction
    rule: str | None
    providers: tuple[str, ...] = ()
    remediation: Remediation | None = None


class RuleEngine:
    """Evaluates events against a rule set.

    Rules are evaluated in declaration order; the first rule whose ``when``
    conditions all match wins. If no rule matches, the engine falls back to
    ``log`` (events are always persisted and logged).
    """

    def __init__(self, rules_file: RulesFile) -> None:
        self._rules: list[Rule] = rules_file.rules

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    def evaluate(self, event: dict[str, Any]) -> RuleDecision:
        for rule in self._rules:
            if self._matches(rule.when, event):
                return RuleDecision(
                    action=rule.action,
                    rule=rule.name,
                    providers=tuple(rule.providers),
                    remediation=rule.remediation,
                )
        return RuleDecision(action=RuleAction.LOG, rule=None)

    @staticmethod
    def _matches(conditions: dict[str, Any], event: dict[str, Any]) -> bool:
        for field, expected in conditions.items():
            actual = event.get(field)
            if not RuleEngine._field_matches(field, actual, expected):
                return False
        return True

    @staticmethod
    def _field_matches(field: str, actual: Any, expected: Any) -> bool:
        if field == "tags":
            tags = actual or []
            if isinstance(expected, list):
                return bool(set(tags) & set(expected))
            return any(fnmatch.fnmatch(tag, expected) for tag in tags)

        if isinstance(expected, list):
            return actual in expected

        if isinstance(actual, str) and isinstance(expected, str):
            return fnmatch.fnmatch(actual, expected)

        if isinstance(expected, str):
            return actual is not None and fnmatch.fnmatch(str(actual), expected)

        return actual == expected
