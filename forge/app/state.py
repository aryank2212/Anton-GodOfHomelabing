"""Persistent state for Forge: last-known-good image tags and git refs.

Stored as a small JSON file (``forge-state.json``) so rollbacks have a real
"last-known-good" baseline that survives restarts. Writes are serialised with
an asyncio lock and flushed atomically.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


class StateManager:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._lock = asyncio.Lock()
        self._data: dict[str, Any] = {"known_good_images": {}, "known_good_git": {}}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = {
                    "known_good_images": raw.get("known_good_images") or {},
                    "known_good_git": raw.get("known_good_git") or {},
                }
        except (OSError, ValueError):
            pass

    async def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), prefix=".forge-state-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, sort_keys=True)
            os.replace(tmp, self._path)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(tmp)

    async def set_known_good_image(self, target: str, image: str) -> None:
        async with self._lock:
            images = self._data["known_good_images"]
            images[target] = {"image": image, "recorded_at": time.time()}
            await self._save()

    def known_good_image(self, target: str) -> str | None:
        entry = self._data.get("known_good_images", {}).get(target)
        return entry.get("image") if entry else None

    def all_known_good_images(self) -> dict[str, dict[str, Any]]:
        return dict(self._data.get("known_good_images", {}))

    async def set_known_good_git(self, repo: str, ref: str) -> None:
        async with self._lock:
            refs = self._data["known_good_git"]
            refs[repo] = {"ref": ref, "recorded_at": time.time()}
            await self._save()

    def known_good_git(self, repo: str) -> str | None:
        entry = self._data.get("known_good_git", {}).get(repo)
        return entry.get("ref") if entry else None
