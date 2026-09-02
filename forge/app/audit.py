"""Append-only audit log for every tool call and its disposition.

JSONL under ``forge-audit.jsonl``. Line format (one object per line):

    {"id","ts","tool","target","args","caller","decision","ok","error","output","approval_id"}

The log is the source of truth for rate limiting and crash-loop detection.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._lock = asyncio.Lock()

    async def record(self, entry: dict[str, Any]) -> str:
        entry_id = secrets.token_hex(6)
        entry.setdefault("id", entry_id)
        entry.setdefault("ts", datetime.now(UTC).isoformat())
        line = json.dumps(entry, default=str, sort_keys=True)
        async with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return entry_id

    async def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self._lock:
            try:
                lines = self._path.read_text(encoding="utf-8").splitlines()
            except OSError:
                return []
        rows: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows

    async def count_recent(self, *, tool: str | None, target: str | None, seconds: float) -> int:
        """Count executions of a tool / tool+target in the last ``seconds``."""
        cutoff = datetime.now(UTC).timestamp() - seconds
        recent = await self.recent(limit=10_000)
        n = 0
        for row in recent:
            try:
                ts = datetime.fromisoformat(row["ts"]).timestamp()
            except (ValueError, KeyError, TypeError):
                continue
            if ts < cutoff:
                continue
            if tool is not None and row.get("tool") != tool:
                continue
            if target is not None and row.get("target") != target:
                continue
            if row.get("decision") not in ("auto", "approval"):
                continue
            n += 1
        return n

    async def last_executed(self, *, tool: str, target: str) -> datetime | None:
        rows = await self.recent(limit=10_000)
        for row in reversed(rows):
            if row.get("tool") == tool and row.get("target") == target:
                try:
                    return datetime.fromisoformat(row["ts"])
                except (ValueError, TypeError):
                    return None
        return None
