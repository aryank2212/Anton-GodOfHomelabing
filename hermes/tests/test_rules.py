from __future__ import annotations

import pytest
from app.rules.engine import RuleEngine
from app.rules.loader import load_rules
from app.rules.models import RuleAction, RulesFile

RULES_YAML = """
version: 1
rules:
  - name: "ignore_debug_watcher"
    action: ignore
    when:
      module: "watcher"
      severity: "debug"

  - name: "notify_errors"
    action: notify
    when:
      severity: "error"
    providers: ["discord", "telegram"]

  - name: "default"
    action: log
    when: {}
"""


def event(**overrides) -> dict:
    base = {
        "id": "evt",
        "module": "watcher",
        "type": "disk.usage",
        "severity": "info",
        "title": "t",
        "message": "m",
        "metadata": {},
        "tags": ["storage"],
        "correlation_id": None,
    }
    base.update(overrides)
    return base


@pytest.fixture
def engine(tmp_path) -> RuleEngine:
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(RULES_YAML, encoding="utf-8")
    return RuleEngine(load_rules(str(rules_file)))


def test_first_match_ignore(engine) -> None:
    decision = engine.evaluate(event(module="watcher", severity="debug"))
    assert decision.action is RuleAction.IGNORE
    assert decision.rule == "ignore_debug_watcher"


def test_notify_matches_providers(engine) -> None:
    decision = engine.evaluate(event(severity="error"))
    assert decision.action is RuleAction.NOTIFY
    assert decision.providers == ("discord", "telegram")


def test_default_fallback(engine) -> None:
    decision = engine.evaluate(event(severity="info", module="phoenix"))
    assert decision.action is RuleAction.LOG
    assert decision.rule == "default"
    assert decision.providers == ()


def test_empty_rules_fall_back_to_log(tmp_path) -> None:
    rules_file = tmp_path / "empty.yaml"
    rules_file.write_text("version: 1\nrules: []\n", encoding="utf-8")
    engine = RuleEngine(load_rules(str(rules_file)))
    decision = engine.evaluate(event())
    assert decision.action is RuleAction.LOG


def test_wildcard_matching(tmp_path) -> None:
    rules = RulesFile.model_validate(
        {
            "rules": [
                {
                    "name": "any_watcher_type",
                    "action": "ignore",
                    "when": {"module": "watcher", "type": "disk.*"},
                }
            ]
        }
    )
    engine = RuleEngine(rules)
    assert engine.evaluate(event(type="disk.usage")).action is RuleAction.IGNORE
    assert engine.evaluate(event(type="cpu.usage")).action is RuleAction.LOG


def test_list_value_matching(tmp_path) -> None:
    rules = RulesFile.model_validate(
        {
            "rules": [
                {
                    "name": "critical_or_error",
                    "action": "notify",
                    "when": {"severity": ["critical", "error"]},
                    "providers": ["all"],
                }
            ]
        }
    )
    engine = RuleEngine(rules)
    assert engine.evaluate(event(severity="critical")).action is RuleAction.NOTIFY
    assert engine.evaluate(event(severity="warning")).action is RuleAction.LOG


def test_tag_matching(tmp_path) -> None:
    rules = RulesFile.model_validate(
        {
            "rules": [
                {
                    "name": "tag_storage",
                    "action": "ignore",
                    "when": {"tags": "storage"},
                }
            ]
        }
    )
    engine = RuleEngine(rules)
    assert engine.evaluate(event(tags=["storage"])).action is RuleAction.IGNORE
    assert engine.evaluate(event(tags=["network"])).action is RuleAction.LOG


def test_missing_rules_file_returns_empty(tmp_path) -> None:
    rules_file = tmp_path / "does_not_exist.yaml"
    rules = load_rules(str(rules_file))
    assert rules.rules == []


def test_invalid_rule_rejected() -> None:
    with pytest.raises(ValueError):
        RulesFile.model_validate({"rules": [{"name": "bad", "action": "notify", "when": {}}]})


def test_remediate_rule_requires_remediation() -> None:
    with pytest.raises(ValueError):
        RulesFile.model_validate({"rules": [{"name": "bad", "action": "remediate", "when": {}}]})


def test_remediation_kind_requires_params() -> None:
    with pytest.raises(ValueError):
        RulesFile.model_validate(
            {
                "rules": [
                    {
                        "name": "bad",
                        "action": "remediate",
                        "when": {},
                        "remediation": {"kind": "command"},
                    }
                ]
            }
        )


def test_remediate_rule_parses_and_evaluates() -> None:
    rules = RulesFile.model_validate(
        {
            "rules": [
                {
                    "name": "restart_on_crash",
                    "action": "remediate",
                    "when": {"severity": "critical"},
                    "remediation": {"kind": "docker_restart", "container": "gitea"},
                    "providers": ["telegram"],
                }
            ]
        }
    )
    engine = RuleEngine(rules)
    decision = engine.evaluate(event(severity="critical"))
    assert decision.action is RuleAction.REMEDIATE
    assert decision.rule == "restart_on_crash"
    assert decision.remediation is not None
    assert decision.remediation.kind == "docker_restart"
    assert decision.remediation.container == "gitea"
    assert decision.providers == ("telegram",)
