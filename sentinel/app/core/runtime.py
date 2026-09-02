"""Sentinel runtime — wires observers, engines, storage and publishing.

The runtime is the only place that knows how the pieces fit together:

    Observer -> Observation -> storage
                             -> correlation engine (situations)
                             -> device tracker   (inventory)
                             -> presence engine   (household status)
                            situations/presence/device changes -> Hermes

It never recovers anything and never notifies anyone; it only observes,
correlates, understands and publishes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress
from datetime import datetime

from app.config.loader import load_devices, load_observers_config, load_vendors
from app.config.settings import Settings
from app.core.clients import Clients
from app.core.logging import get_logger
from app.core.publisher import EventPublisher, HermesPublisher, NullPublisher
from app.core.scheduler import ObserverScheduler
from app.correlation.engine import CorrelationEngine, SituationChange
from app.correlation.loader import load_rules
from app.database.repository import Repository
from app.database.session import Database
from app.models.device import DeviceEvent, PresenceChange
from app.models.event import (
    EVENT_DEVICE_JOINED,
    EVENT_DEVICE_LEFT,
    EVENT_DEVICE_UNKNOWN_JOINED,
    EVENT_PRESENCE_CHANGE,
    EVENT_SITUATION_ACTIVATED,
    EVENT_SITUATION_RESOLVED,
    HERMES_SEVERITY,
    HermesEvent,
)
from app.models.observation import Category, Observation, Severity, utcnow
from app.network.tracker import DeviceTracker
from app.network.vendors import VendorLookup
from app.observers.registry import default_registry
from app.presence.definitions import DeviceCatalog
from app.presence.engine import PresenceEngine

log = get_logger(__name__)


class SentinelRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database: Database | None = None
        self.repository: Repository | None = None
        self.tracker: DeviceTracker | None = None
        self.presence: PresenceEngine | None = None
        self.correlation: CorrelationEngine | None = None
        self.publisher: EventPublisher | None = None
        self.clients: Clients | None = None
        self.scheduler: ObserverScheduler | None = None
        self.observers: list = []
        self._tasks: list[asyncio.Task[None]] = []
        self.started_at = utcnow()
        self.last_correlation_tick: datetime | None = None
        self.last_presence_tick: datetime | None = None

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        settings = self.settings
        database = Database(settings.database_url)
        await database.init()
        self.database = database
        self.repository = Repository(database.session_factory)

        rules = load_rules(settings.rules_file)
        devices = load_devices(settings.devices_file).devices
        vendors = load_vendors(settings.vendors_file)
        observers_config = load_observers_config(settings.observers_file)

        catalog = DeviceCatalog(devices)
        self.tracker = DeviceTracker(
            catalog=catalog,
            vendor_lookup=VendorLookup(vendors),
            offline_after=settings.presence_offline_after,
        )
        await self.tracker.load(self.repository)

        self.presence = PresenceEngine(
            offline_after=settings.presence_offline_after,
            recent_window=settings.presence_recent_window,
            empty_confidence=settings.presence_empty_confidence,
        )

        self.correlation = CorrelationEngine(
            rules=rules,
            window_size=settings.correlation_window_size,
            grace_seconds=settings.correlation_grace_seconds,
        )
        await self.correlation.load_recent(self.repository)

        self.clients = Clients.defaults()
        self.publisher = (
            HermesPublisher(settings, client=self.clients.http)
            if settings.hermes_enabled
            else NullPublisher()
        )

        registry = default_registry()
        self.observers = registry.build_all(observers_config, settings, self.clients)
        self.scheduler = ObserverScheduler(
            self.observers,
            default_timeout=settings.observer_timeout,
            jitter=settings.observer_jitter,
        )
        if self.observers:
            self.scheduler.start(self.ingest)

        self._tasks.append(
            asyncio.create_task(
                self._tick(
                    self._correlation_tick, settings.correlation_scan_interval, "correlation"
                )
            )
        )
        self._tasks.append(
            asyncio.create_task(
                self._tick(self._presence_tick, settings.presence_scan_interval, "presence")
            )
        )

        log.info(
            "sentinel_started",
            extra={
                "version": settings.version,
                "environment": settings.environment,
                "observers": [observer.name for observer in self.observers],
                "rules": len(self.correlation.rules),
                "devices": len(devices),
                "hermes": settings.hermes_event_url if settings.hermes_enabled else "disabled",
            },
        )

    async def stop(self) -> None:
        log.info("sentinel_stopping")
        if self.scheduler is not None:
            await self.scheduler.stop()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        if self.publisher is not None:
            await self.publisher.aclose()
        if self.clients is not None:
            await self.clients.aclose()
        if self.database is not None:
            await self.database.dispose()
        log.info("sentinel_stopped")

    # ------------------------------------------------------------------ ticking
    async def _tick(self, handler, interval: float, name: str) -> None:
        try:
            while True:
                await asyncio.sleep(interval)
                await handler()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never kill the runtime
            log.error("tick_failed", extra={"tick": name, "error": str(exc)})

    async def _correlation_tick(self) -> None:
        self.last_correlation_tick = utcnow()
        assert self.correlation is not None
        changes = await self.correlation.evaluate()
        await self._apply_situation_changes(changes)

    async def _presence_tick(self) -> None:
        self.last_presence_tick = utcnow()
        assert self.tracker is not None
        events = self.tracker.reconcile_online()
        if events:
            changed = [self.tracker.get(event.device_key) for event in events]
            assert self.repository is not None
            await self.repository.upsert_devices([d for d in changed if d is not None])
            await self._apply_device_events(events)
        await self.refresh_presence()

    # ------------------------------------------------------------------ ingest
    async def ingest(self, observations: Sequence[Observation]) -> None:
        for observation in observations:
            await self.ingest_one(observation)

    async def ingest_one(self, observation: Observation) -> None:
        assert self.repository is not None
        assert self.tracker is not None
        assert self.correlation is not None

        await self.repository.add_observation(observation)
        changed_devices, events = await self.tracker.feed(observation)
        if changed_devices:
            await self.repository.upsert_devices(changed_devices)
        if events:
            await self._apply_device_events(events)
        changes = await self.correlation.feed(observation)
        await self._apply_situation_changes(changes)
        if events:
            await self.refresh_presence()

    async def refresh_presence(self) -> None:
        assert self.repository is not None
        assert self.tracker is not None
        assert self.presence is not None
        assert self.correlation is not None

        change, observation = self.presence.recompute(self.tracker.snapshot())
        await self.repository.add_observation(observation)
        changes = await self.correlation.feed(observation)
        await self._apply_situation_changes(changes)
        if change is not None:
            await self.repository.add_presence_sample(change.current)
            await self._publish_presence(change)

    # -------------------------------------------------------------- apply changes
    async def _apply_situation_changes(self, changes: list[SituationChange]) -> None:
        assert self.repository is not None
        for change in changes:
            await self.repository.save_situation(change.situation)
            if change.action in ("activated", "resolved") and change.publish:
                await self._publish_situation(change)

    async def _apply_device_events(self, events: list[DeviceEvent]) -> None:
        assert self.repository is not None
        assert self.correlation is not None
        for event in events:
            await self.repository.add_device_event(event)
            await self._publish_device_event(event)
            if event.event not in ("joined", "left"):
                continue
            observation = Observation(
                source=event.source or "device",
                category=Category.NETWORK,
                severity=Severity.INFO,
                object=f"device:{event.device_key}",
                state=event.event,
                confidence=0.95,
                metadata={
                    "mac": event.mac,
                    "ip": event.ip,
                    "hostname": event.hostname,
                    "known": event.known,
                },
                tags=["device", event.event, "known" if event.known else "unknown"],
            )
            await self.repository.add_observation(observation)
            changes = await self.correlation.feed(observation)
            await self._apply_situation_changes(changes)

    # ---------------------------------------------------------------- publish
    async def _publish_presence(self, change: PresenceChange) -> None:
        state = change.current
        previous = change.previous.value if change.previous else None
        assert self.publisher is not None
        await self.publisher.publish(
            HermesEvent(
                type=EVENT_PRESENCE_CHANGE,
                severity="info",
                title=state.label,
                message=(
                    f"Presence is now '{state.label}' "
                    f"({', '.join(state.people) if state.people else 'no known owner'}) "
                    f"[previously {previous or 'unknown'}]."
                ),
                metadata={
                    "status": state.status.value,
                    "previous": previous,
                    "confidence": state.confidence,
                    "people": state.people,
                },
                tags=["presence"],
            )
        )

    async def _publish_situation(self, change: SituationChange) -> None:
        situation = change.situation
        activated = change.action == "activated"
        assert self.publisher is not None
        await self.publisher.publish(
            HermesEvent(
                type=EVENT_SITUATION_ACTIVATED if activated else EVENT_SITUATION_RESOLVED,
                severity=HERMES_SEVERITY[situation.severity.value],
                title=f"{'Activated' if activated else 'Resolved'}: {situation.name}",
                message=situation.summary,
                metadata={
                    "situation_id": str(situation.situation_id),
                    "rule_id": situation.rule_id,
                    "status": situation.status.value,
                    "confidence": situation.confidence,
                    "sources": situation.sources,
                },
                tags=["situation", situation.type],
            )
        )

    async def _publish_device_event(self, event: DeviceEvent) -> None:
        if event.event == "joined":
            if not event.known:
                event_type = EVENT_DEVICE_UNKNOWN_JOINED
                title = "Unknown Device Joined"
                severity = "warning"
            else:
                event_type = EVENT_DEVICE_JOINED
                title = "Device Joined"
                severity = "info"
            verb = "connected to the network"
        elif event.event == "left":
            event_type = EVENT_DEVICE_LEFT
            title = "Device Left"
            severity = "info"
            verb = "disconnected from the network"
        else:
            return
        identity = event.hostname or event.mac or event.device_key
        assert self.publisher is not None
        await self.publisher.publish(
            HermesEvent(
                type=event_type,
                severity=severity,
                title=title,
                message=f"{identity} {verb}.",
                metadata={
                    "device_key": event.device_key,
                    "mac": event.mac,
                    "ip": event.ip,
                    "hostname": event.hostname,
                    "known": event.known,
                },
                tags=["device", event.event, "known" if event.known else "unknown"],
            )
        )
