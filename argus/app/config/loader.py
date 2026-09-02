"""YAML configuration loading for Argus.

All the *description* of what Argus watches lives in YAML: feeds, scrape
targets, OSINT providers and Telegram channels, each with its own tuning. The
loaders here parse and validate the files so the rest of the code only ever
sees typed objects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from app.core.logging import get_logger

log = get_logger(__name__)


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load a YAML file, returning an empty mapping when it is missing."""
    file = Path(path)
    if not file.exists():
        log.warning("config_missing", extra={"path": str(file)})
        return {}
    with file.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


class SourceSpec(BaseModel):
    """Tuning for a single source (the dict key is its collector name)."""

    enabled: bool = True
    interval: float | None = None
    timeout: float | None = None
    # rss | scrape | osint | telegram — the collector that owns this source.
    kind: str = "rss"
    params: dict[str, Any] = Field(default_factory=dict)


class SourcesConfig(BaseModel):
    version: int = 1
    sources: dict[str, SourceSpec] = Field(default_factory=dict)


def load_sources_config(path: str | Path) -> SourcesConfig:
    raw = load_yaml(path)
    return SourcesConfig.model_validate(raw)
