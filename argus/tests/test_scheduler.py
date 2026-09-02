from __future__ import annotations

import asyncio
from collections.abc import Sequence

from app.core.scheduler import CollectorScheduler
from app.models.content import ContentItem, SourceType
from app.sources.base import Collector


class ScriptedCollector(Collector):
    """A collector whose behaviour is scripted by ``script``.

    Each run blocks on the ``tick`` event (so the test controls exactly one run
    per step) and then either collects an item (``True``) or raises (``False``).
    An exhausted script keeps succeeding. After every run a ``done`` event is
    set so the test can wait for a specific execution to finish and assert on
    the resulting state.
    """

    name = "scripted"

    def __init__(
        self,
        script: list[bool] | None = None,
        *,
        name: str = "scripted",
        tick: asyncio.Event,
        done: asyncio.Event,
    ) -> None:
        super().__init__(default_interval=0.01, default_timeout=0.1)
        self.script = list(script or [])
        self.name = name
        self._tick = tick
        self._done = done

    async def collect(self) -> Sequence[ContentItem]:
        await self._tick.wait()
        self._tick.clear()
        ok = self.script.pop(0) if self.script else True
        try:
            if not ok:
                raise RuntimeError("boom")
            return [
                ContentItem(
                    source=self.name,
                    source_type=SourceType.RSS,
                    url=f"http://example.com/{self.name}",
                    title=self.name,
                )
            ]
        finally:
            self._done.set()


class RecordingPublisher:
    def __init__(self, *, enabled: bool = True) -> None:
        self.events: list = []
        self.enabled = enabled

    async def publish(self, event) -> bool:
        self.events.append(event)
        return True


async def _step(collector: ScriptedCollector) -> None:
    # Advance one script value and wait until the scheduler has processed the run.
    collector._done.clear()
    collector._tick.set()
    for _ in range(50):
        if collector._done.is_set():
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("collector did not complete its run")


async def _make_scheduler(
    script: list[bool] | None,
    *,
    threshold: int = 3,
    publisher_enabled: bool = True,
):
    tick = asyncio.Event()
    done = asyncio.Event()
    collector = ScriptedCollector(script, name="src", tick=tick, done=done)
    publisher = RecordingPublisher(enabled=publisher_enabled)
    scheduler = CollectorScheduler(
        [collector],
        default_timeout=0.1,
        jitter=0.0,
        backoff_base=1.0,
        backoff_max=1000.0,
        failure_threshold=threshold,
        publisher=publisher,
        sleep=lambda _: asyncio.sleep(0),
    )
    scheduler.start(lambda items: asyncio.sleep(0))
    return collector, scheduler, publisher


async def test_under_threshold_no_degrade() -> None:
    collector, scheduler, publisher = await _make_scheduler(
        [False, False, True], threshold=3
    )
    try:
        for _ in range(3):
            await _step(collector)
        status = scheduler.status("src")
        assert status is not None
        assert status.degraded is False
        assert status.consecutive_failures == 0
        assert status.backoff == 0.0
        assert status.last_error is None
        assert publisher.events == []
    finally:
        await scheduler.stop()


async def test_degrade_emits_event_and_grows_backoff() -> None:
    collector, scheduler, publisher = await _make_scheduler(
        [False, False, False], threshold=3
    )
    try:
        for _ in range(3):
            await _step(collector)
        status = scheduler.status("src")
        assert status is not None
        assert status.degraded is True
        assert status.consecutive_failures == 3
        assert status.last_error is not None
        # 3rd failure doubled twice from base 1.0: 1 * 2^(3-1) = 4
        assert status.backoff == 4.0

        assert len(publisher.events) == 1
        event = publisher.events[0]
        assert event.type == "collector_degraded"
        assert event.severity == "warning"
        assert "src" in event.title
    finally:
        await scheduler.stop()


async def test_recovery_resets_and_emits_event() -> None:
    collector, scheduler, publisher = await _make_scheduler(
        [False, False, False, True], threshold=2
    )
    try:
        for _ in range(4):
            await _step(collector)
        status = scheduler.status("src")
        assert status is not None
        assert status.degraded is False
        assert status.consecutive_failures == 0
        assert status.backoff == 0.0
        assert status.last_error is None

        types = [e.type for e in publisher.events]
        assert types == ["collector_degraded", "collector_recovered"]
    finally:
        await scheduler.stop()


async def test_disabled_publisher_emits_no_events() -> None:
    collector, scheduler, publisher = await _make_scheduler(
        [False, False, False], threshold=2, publisher_enabled=False
    )
    try:
        for _ in range(3):
            await _step(collector)
        status = scheduler.status("src")
        assert status is not None
        assert status.degraded is True
        assert publisher.events == []
    finally:
        await scheduler.stop()
