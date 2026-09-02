"""State manager: known-good image and git ref persistence."""

from __future__ import annotations

import json

from app.state import StateManager


async def test_known_good_image_roundtrip(tmp_path) -> None:
    state = StateManager(str(tmp_path / "state.json"))
    await state.set_known_good_image("gitea", "gitea/gitea:1.21.0")
    assert state.known_good_image("gitea") == "gitea/gitea:1.21.0"
    assert state.known_good_image("immich") is None

    reloaded = StateManager(str(tmp_path / "state.json"))
    assert reloaded.known_good_image("gitea") == "gitea/gitea:1.21.0"


async def test_known_good_git_roundtrip(tmp_path) -> None:
    state = StateManager(str(tmp_path / "state.json"))
    await state.set_known_good_git("/opt/anton/phoenix", "deadbeef")
    assert state.known_good_git("/opt/anton/phoenix") == "deadbeef"


async def test_state_file_is_valid_json(tmp_path) -> None:
    state = StateManager(str(tmp_path / "state.json"))
    await state.set_known_good_image("a", "img")
    raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert raw["known_good_images"]["a"]["image"] == "img"


def test_missing_state_file_ok(tmp_path) -> None:
    state = StateManager(str(tmp_path / "nope.json"))
    assert state.known_good_image("gitea") is None
