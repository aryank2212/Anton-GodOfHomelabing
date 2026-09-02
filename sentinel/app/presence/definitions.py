"""Device catalog — the known-device registry.

Declared in ``devices.yaml``, the catalog is the source of truth for what
Sentinel considers a *known* device and which human (if any) owns it. The
device tracker uses it to name devices; the presence engine uses it to infer
people at home. It performs no biometric or AI recognition.
"""

from __future__ import annotations

from app.config.loader import DeviceDefinition
from app.network.vendors import normalize_mac


class DeviceCatalog:
    def __init__(self, definitions: list[DeviceDefinition]) -> None:
        self.definitions = definitions
        self._by_mac: dict[str, DeviceDefinition] = {}
        self._by_ip: dict[str, DeviceDefinition] = {}
        for definition in definitions:
            if definition.mac:
                self._by_mac[normalize_mac(definition.mac)] = definition
            if definition.ip:
                self._by_ip[definition.ip] = definition

    def match(
        self,
        mac: str | None = None,
        ip: str | None = None,
        hostname: str | None = None,
    ) -> DeviceDefinition | None:
        if mac:
            definition = self._by_mac.get(normalize_mac(mac))
            if definition:
                return definition
        if ip:
            definition = self._by_ip.get(ip)
            if definition:
                return definition
        if hostname:
            lowered = hostname.lower()
            for definition in self.definitions:
                aliases = [definition.hostname or "", *definition.aliases]
                if lowered in {alias.lower() for alias in aliases if alias}:
                    return definition
                if definition.name.lower() in lowered:
                    return definition
        return None

    def owner_of(self, mac: str | None = None, ip: str | None = None) -> str | None:
        definition = self.match(mac=mac, ip=ip)
        return definition.owner if definition else None
