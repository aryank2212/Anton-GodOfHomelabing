from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


def make_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "module": "watcher",
        "type": "disk.usage",
        "severity": "warning",
        "title": "Disk usage high",
        "message": "/data is at 85%",
        "metadata": {"usage_percent": 85.0, "mount": "/data"},
        "tags": ["storage", "watcher"],
    }
    payload.update(overrides)
    return payload


async def wait_until(condition: Callable[[], Any], timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met within timeout")


async def event_state(state, event_id: str) -> str:
    from app.database.models import EventRecord

    async with state.database.session_factory() as session:
        event = await session.get(EventRecord, event_id)
    return event.state if event else "missing"


async def event_outcome(state, event_id: str) -> str:
    from app.database.models import EventRecord

    async with state.database.session_factory() as session:
        event = await session.get(EventRecord, event_id)
    return event.outcome if event else "missing"


async def state_is(state, event_id: str, expected: str) -> bool:
    return (await event_state(state, event_id)) == expected


async def outcome_is(state, event_id: str, expected: str) -> bool:
    return (await event_outcome(state, event_id)) == expected
