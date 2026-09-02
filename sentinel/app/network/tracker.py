"""Device tracker — the in-memory device inventory.

The tracker converts raw network observations into a consistent view of the
device fleet: known/unknown, vendor, IPs, hostnames, first/last seen and
online state. It emits lifecycle events (joined / left / seen) that the
runtime persists and turns into observations.

The tracker holds no AI, no secrets and makes no decisions about people — it
only records what the network reported.
"""

from __future__ import annotations

from datetime import datetime

from app.config.loader import DeviceDefinition
from app.core.logging import get_logger
from app.models.device import Device, DeviceEvent, DeviceKind
from app.models.observation import Observation, utcnow
from app.network.vendors import VendorLookup, normalize_mac
from app.presence.definitions import DeviceCatalog

log = get_logger(__name__)

_ONLINE_STATES = {"connected", "joined", "seen", "present", "online", "up", "on_line"}
_OFFLINE_STATES = {"offline", "disconnected", "left", "down", "away", "unreachable"}


def _kind_from_definition(definition: DeviceDefinition | None) -> DeviceKind:
    if definition is None:
        return DeviceKind.UNKNOWN
    try:
        return DeviceKind(definition.category)
    except ValueError:
        return DeviceKind.UNKNOWN


class DeviceTracker:
    def __init__(
        self,
        *,
        catalog: DeviceCatalog,
        vendor_lookup: VendorLookup,
        offline_after: float = 180.0,
    ) -> None:
        self._catalog = catalog
        self._vendors = vendor_lookup
        self._offline_after = offline_after
        self._by_key: dict[str, Device] = {}
        self._by_mac: dict[str, str] = {}

    # ------------------------------------------------------------------ setup
    @property
    def definitions(self) -> list[DeviceDefinition]:
        return self._catalog.definitions

    async def load(self, repository) -> None:
        """Rebuild state from persisted devices at startup."""
        devices, _total = await repository.list_devices(limit=100_000)
        for device in devices:
            self._by_key[device.device_key] = device
            if device.mac:
                self._by_mac[device.mac] = device.device_key
        log.info("device_tracker_loaded", extra={"devices": len(devices)})

    def snapshot(self) -> list[Device]:
        return sorted(self._by_key.values(), key=lambda d: d.display_name.lower())

    def get(self, device_key: str) -> Device | None:
        return self._by_key.get(device_key)

    def definition_for(self, observation: Observation) -> DeviceDefinition | None:
        mac = observation.metadata.get("mac")
        ip = observation.metadata.get("ip")
        hostname = observation.metadata.get("hostname")
        return self._catalog.match(mac=mac, ip=ip, hostname=hostname)

    # ------------------------------------------------------------------ feed
    async def feed(self, observation: Observation) -> tuple[list[Device], list[DeviceEvent]]:
        """Process one observation; return (changed devices, lifecycle events)."""
        info = self._extract_info(observation)
        if info is None:
            return [], []

        now = utcnow()
        key = self._identity_key(info)
        definition = self.definition_for(observation)
        known = definition is not None
        vendor = self._vendors.lookup(info.get("mac"))

        existing = self._by_key.get(key)
        if existing is None and info.get("mac"):
            existing = self._by_key.get(self._by_mac.get(info["mac"], ""))

        online = observation.state in _ONLINE_STATES

        if existing is None:
            device = self._new_device(
                key, info, definition, known, vendor, online, observation, now
            )
            self._by_key[key] = device
            if device.mac:
                self._by_mac[device.mac] = key
            initial_events = [self._joined_event(device, observation)] if online else []
            return [device], initial_events

        changed, device = self._merge(
            existing, info, definition, known, vendor, online, observation, now
        )
        self._by_key[key] = device
        if device.mac and existing.mac != device.mac:
            self._by_mac[device.mac] = key

        events: list[DeviceEvent] = []
        if online and not existing.online:
            events.append(self._joined_event(device, observation))
        elif not online and existing.online:
            events.append(self._left_event(device, observation))
        elif changed:
            events.append(self._seen_event(device, observation))

        return ([device] if changed else []), events

    def reconcile_online(self, now: datetime | None = None) -> list[DeviceEvent]:
        """Mark devices offline when last seen too long ago; return left events."""
        now = now or utcnow()
        events: list[DeviceEvent] = []
        for key, device in list(self._by_key.items()):
            if not device.online or device.last_seen is None:
                continue
            if (now - device.last_seen).total_seconds() > self._offline_after:
                updated = device.model_copy(update={"online": False, "updated_at": now})
                self._by_key[key] = updated
                events.append(
                    DeviceEvent(
                        device_key=key,
                        event="left",
                        timestamp=now,
                        mac=device.mac,
                        ip=device.ips[0] if device.ips else None,
                        hostname=device.hostnames[0] if device.hostnames else None,
                        source="tracker",
                        known=device.known,
                    )
                )
        return events

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _extract_info(observation: Observation) -> dict[str, str] | None:
        meta = observation.metadata or {}
        mac = meta.get("mac")
        ip = meta.get("ip")
        hostname = meta.get("hostname")
        if not (mac or ip or hostname):
            if observation.object.startswith("device:"):
                identity = observation.object.split(":", 1)[1]
                if ":" in identity and len(identity) >= 12:
                    mac = identity
                else:
                    ip = identity
            else:
                return None
        info: dict[str, str] = {}
        if mac:
            info["mac"] = normalize_mac(str(mac))
        if ip:
            info["ip"] = str(ip)
        if hostname:
            info["hostname"] = str(hostname)
        return info or None

    @staticmethod
    def _identity_key(info: dict[str, str]) -> str:
        if info.get("mac"):
            return info["mac"]
        if info.get("ip"):
            return f"ip:{info['ip']}"
        return f"host:{info['hostname']}"

    def _new_device(
        self,
        key: str,
        info: dict[str, str],
        definition: DeviceDefinition | None,
        known: bool,
        vendor: str | None,
        online: bool,
        observation: Observation,
        now: datetime,
    ) -> Device:
        return Device(
            device_key=key,
            mac=info.get("mac"),
            name=definition.name if definition else (info.get("hostname") or ""),
            known=known,
            owner=definition.owner if definition else None,
            category=_kind_from_definition(definition),
            vendor=vendor,
            ips=[info["ip"]] if info.get("ip") else [],
            hostnames=[info["hostname"]] if info.get("hostname") else [],
            first_seen=now,
            last_seen=now if online else None,
            online=online,
            confidence=observation.confidence,
            metadata={
                "source": observation.source,
                "first_observation_id": str(observation.observation_id),
            },
            updated_at=now,
        )

    def _merge(
        self,
        existing: Device,
        info: dict[str, str],
        definition: DeviceDefinition | None,
        known: bool,
        vendor: str | None,
        online: bool,
        observation: Observation,
        now: datetime,
    ) -> tuple[bool, Device]:
        updates: dict[str, object] = {"updated_at": now}
        changed = False

        if known and existing.name != (definition.name if definition else existing.name):
            updates["name"] = definition.name if definition else existing.name
            changed = True
        if known != existing.known:
            updates["known"] = known
            changed = True
        if definition and definition.owner and definition.owner != existing.owner:
            updates["owner"] = definition.owner
            changed = True
        if vendor and vendor != existing.vendor:
            updates["vendor"] = vendor
            changed = True

        if online and not existing.online:
            updates["online"] = True
            updates["last_seen"] = now
            changed = True
        elif not online and existing.online:
            updates["online"] = False
            changed = True
        elif online:
            updates["last_seen"] = now

        ips = list(existing.ips)
        if info.get("ip") and info["ip"] not in ips:
            ips.append(info["ip"])
            changed = True
        hostnames = list(existing.hostnames)
        if info.get("hostname") and info["hostname"] not in hostnames:
            hostnames.append(info["hostname"])
            changed = True
        updates["ips"] = ips
        updates["hostnames"] = hostnames

        if existing.confidence < observation.confidence:
            updates["confidence"] = observation.confidence
            changed = True

        return changed, existing.model_copy(update=updates)

    @staticmethod
    def _joined_event(device: Device, observation: Observation) -> DeviceEvent:
        return DeviceEvent(
            device_key=device.device_key,
            event="joined",
            mac=device.mac,
            ip=device.ips[0] if device.ips else None,
            hostname=device.hostnames[0] if device.hostnames else None,
            source=observation.source,
            known=device.known,
            metadata={"confidence": observation.confidence},
        )

    @staticmethod
    def _left_event(device: Device, observation: Observation) -> DeviceEvent:
        return DeviceEvent(
            device_key=device.device_key,
            event="left",
            mac=device.mac,
            ip=device.ips[0] if device.ips else None,
            hostname=device.hostnames[0] if device.hostnames else None,
            source=observation.source,
            known=device.known,
            metadata={"confidence": observation.confidence},
        )

    @staticmethod
    def _seen_event(device: Device, observation: Observation) -> DeviceEvent:
        return DeviceEvent(
            device_key=device.device_key,
            event="seen",
            mac=device.mac,
            ip=device.ips[0] if device.ips else None,
            hostname=device.hostnames[0] if device.hostnames else None,
            source=observation.source,
            known=device.known,
            metadata={"confidence": observation.confidence},
        )
