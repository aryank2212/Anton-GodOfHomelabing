"""Loader for correlation rules (``rules.yaml``)."""

from __future__ import annotations

from pathlib import Path

from app.config.loader import load_yaml
from app.core.logging import get_logger
from app.correlation.rules import Rule, RulesFile

log = get_logger(__name__)


def load_rules(path: str | Path) -> list[Rule]:
    raw = load_yaml(path)
    rules_file = RulesFile.model_validate(raw)
    for rule in rules_file.rules:
        rule.normalize_condition_windows()
    log.info(
        "rules_loaded",
        extra={"path": str(path), "count": len(rules_file.rules)},
    )
    return rules_file.rules
