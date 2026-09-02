from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.database.models import EventRecord

log = get_logger(__name__)

Processor = Callable[[str], Awaitable[None]]


class NotificationQueue:
    """In-process async delivery queue with an outbox sweep.

    How it works:

    * The HTTP layer writes events to the database and then calls ``put`` to
      enqueue the event id. Enqueuing never blocks the request.
    * A pool of worker tasks claims the event (state ``pending`` ->
      ``processing``) and hands it to the processor (the dispatcher).
    * A periodic sweep re-enqueues any event that is still ``pending``. This
      makes the system self-healing: if the process dies mid-dispatch, the
      event is picked up again on the next sweep (or on restart).

    The class deliberately exposes a tiny surface (``start`` / ``stop`` /
    ``put`` / ``size``) so the transport can later be replaced by Redis or a
    message broker without changing any caller.
    """

    def __init__(
        self,
        processor: Processor,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        concurrency: int,
        sweep_interval: float,
    ) -> None:
        self._processor = processor
        self._session_factory = session_factory
        self._concurrency = concurrency
        self._sweep_interval = sweep_interval
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._sweep_task: asyncio.Task[None] | None = None
        self._running = False

    def put(self, event_id: str) -> None:
        self._queue.put_nowait(event_id)

    def size(self) -> int:
        return self._queue.qsize()

    async def start(self) -> None:
        self._running = True
        await self._recover_stale_events()
        self._workers = [
            asyncio.create_task(self._worker(index), name=f"hermes-worker-{index}")
            for index in range(self._concurrency)
        ]
        self._sweep_task = asyncio.create_task(self._sweep(), name="hermes-sweep")

    async def stop(self) -> None:
        self._running = False
        for task in [self._sweep_task, *self._workers]:
            if task is not None:
                task.cancel()
        for task in [self._sweep_task, *self._workers]:
            if task is not None:
                with suppress(asyncio.CancelledError):
                    await task

    async def _recover_stale_events(self) -> None:
        """Crash recovery: reset interrupted events and requeue pending ones."""
        async with self._session_factory() as session:
            result = await session.execute(
                update(EventRecord).where(EventRecord.state == "processing").values(state="pending")
            )
            rowcount = 0
            if isinstance(result, CursorResult):
                rowcount = result.rowcount or 0
            if rowcount:
                log.info("stale_processing_events_reset", extra={"count": rowcount})
            await session.commit()
        await self._enqueue_pending_batch()

    async def _enqueue_pending_batch(self) -> None:
        async with self._session_factory() as session:
            ids = (
                (
                    await session.execute(
                        select(EventRecord.id)
                        .where(EventRecord.state == "pending")
                        .order_by(EventRecord.created_at)
                        .limit(500)
                    )
                )
                .scalars()
                .all()
            )
        for event_id in ids:
            self.put(event_id)
        if ids:
            log.info("outbox_requeued", extra={"count": len(ids)})

    async def _worker(self, index: int) -> None:
        log.info("worker_started", extra={"worker": index})
        try:
            while self._running:
                event_id = await self._queue.get()
                try:
                    await self._processor(event_id)
                except Exception:
                    # Leave the event in a recoverable state so the sweep
                    # (or a restart) retries it.
                    log.exception("event_processing_failed", extra={"event_id": event_id})
                    await self._release(event_id)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            log.info("worker_stopped", extra={"worker": index})
            raise

    async def _release(self, event_id: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(EventRecord)
                .where(EventRecord.id == event_id, EventRecord.state == "processing")
                .values(state="pending")
            )
            await session.commit()

    async def _sweep(self) -> None:
        while self._running:
            await asyncio.sleep(self._sweep_interval)
            try:
                await self._enqueue_pending_batch()
            except Exception:
                log.exception("outbox_sweep_failed")
