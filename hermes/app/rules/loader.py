from __future__ import annotations

from pathlib import Path

import yaml

from app.core.logging import get_logger
from app.rules.models import RulesFile

log = get_logger(__name__)


def load_rules(path: str | Path) -> RulesFile:
    """Load and validate rules from a YAML file.

    Missing file -> empty rule set (the engine then falls back to ``log``).
    Malformed YAML / invalid schema -> the error is raised: failing fast beats
    silently running without intended notification rules.
    """
    path = Path(path)
    if not path.is_file():
        log.warning("rules_file_not_found", extra={"path": str(path)})
        return RulesFile()

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        return RulesFile.model_validate(raw)
    except Exception:
        log.exception("rules_file_invalid", extra={"path": str(path)})
        raise
