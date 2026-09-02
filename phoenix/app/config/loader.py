"""Loading of the YAML Phoenix configuration into typed models."""

from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]  # PyYAML ships no stubs

from app.config.models import PhoenixConfig
from app.core.logging import get_logger

log = get_logger(__name__)


def load_config(path: str | Path) -> PhoenixConfig:
    """Parse and validate a ``phoenix.yaml`` file.

    Raises ``ValueError`` (or ``yaml.YAMLError``) on invalid configuration so
    a misconfigured Phoenix refuses to start instead of limping along.
    """
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"config file '{path}' does not contain a YAML mapping")
    if "phoenix" in raw:
        raw = raw["phoenix"]
    config = PhoenixConfig.model_validate(raw)
    log.info(
        "config_loaded",
        extra={
            "path": str(config_path),
            "monitors": len(config.monitors),
            "components": len(config.components),
        },
    )
    return config
