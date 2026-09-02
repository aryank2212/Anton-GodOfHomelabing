"""Shared helpers for Argus models."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Timezone-aware UTC now used across Argus."""
    return datetime.now(UTC)
