"""MAC OUI vendor lookup.

A small built-in table covers the most common consumer device vendors; the
table is merged with the administrator's ``vendors.yaml`` additions. The
``lookup`` method returns the most specific known match for a MAC address.
"""

from __future__ import annotations

BUILTIN_VENDORS: dict[str, str] = {
    "a4:83:e7": "Apple",
    "f0:18:98": "Apple",
    "ac:bc:32": "Apple",
    "3c:22:fb": "Apple",
    "f0:d5:bf": "Apple",
    "44:d8:84": "Apple",
    "58:02:05": "Dell",
    "3c:06:30": "Lenovo",
    "00:9e:c8": "Dell",
    "04:4b:ed": "Samsung",
    "98:9d:b2": "AVM",
    "8c:ef:40": "ASUSTek",
    "9c:8b:f4": "ASUSTek",
    "e8:48:b8": "ASUSTek",
    "dc:0b:1a": "ASUSTek",
    "d8:32:14": "Raspberry Pi",
    "b8:27:eb": "Raspberry Pi",
    "dc:a6:32": "Raspberry Pi",
    "e4:5f:01": "Raspberry Pi",
    "3c:5a:b4": "Raspberry Pi",
    "34:97:f6": "Espressif",
    "24:0a:c4": "Espressif",
    "a4:cf:12": "Espressif",
    "50:02:91": "Espressif",
    "24:0f:5e": "Huawei",
    "0c:9d:92": "Hon Hai",
    "40:8d:5c": "Amazon",
    "44:65:0d": "Amazon",
    "f0:27:2d": "Amazon",
    "8c:7b:9d": "TP-Link",
    "50:c7:bf": "TP-Link",
    "64:6e:97": "TP-Link",
    "1c:4b:d6": "TP-Link",
    "f4:f2:6d": "TP-Link",
    "28:c6:8e": "TP-Link",
    "bc:0f:2f": "TP-Link",
    "a0:63:91": "Intel",
    "3c:7c:3f": "Intel",
    "b4:2e:99": "Intel",
    "f8:75:a4": "Intel",
    "00:1b:21": "Intel",
    "00:50:56": "VMware",
    "00:0c:29": "VMware",
    "08:00:27": "Oracle",
    "52:54:00": "QEMU",
    "00:15:5d": "Microsoft",
    "3c:d9:2b": "Microsoft",
    "00:1a:79": "Google",
    "f8:34:41": "Google",
    "3c:5a:37": "Google",
    "a4:77:33": "Google",
    "bc:16:65": "Google",
    "d8:bb:c1": "Sony",
    "ec:21:e5": "Sony",
    "50:eb:f6": "Sony",
    "5c:f3:70": "Sony",
    "d8:96:95": "Samsung",
    "58:c2:3d": "Samsung",
    "8c:77:12": "Samsung",
    "50:1a:c2": "Samsung",
    "10:2f:6b": "Samsung",
    "40:16:7e": "Samsung",
    "5c:25:3f": "LG",
    "70:b3:d5": "LG",
    "bc:a8:6f": "LG",
}


def normalize_mac(mac: str) -> str:
    """Normalize a MAC to lower-case colon form (``a4:83:e7:12:34:56``)."""
    digits = "".join(ch for ch in mac.strip().lower() if ch in "0123456789abcdef")
    if len(digits) < 12:
        return mac.strip().lower()
    return ":".join(digits[i : i + 2] for i in range(0, len(digits), 2))


class VendorLookup:
    """Look up a vendor from a MAC address, most-specific prefix first."""

    def __init__(self, custom: dict[str, str] | None = None) -> None:
        merged: dict[str, str] = dict(BUILTIN_VENDORS)
        for prefix, vendor in (custom or {}).items():
            merged[normalize_mac(prefix)] = vendor
        self._mapping = merged

    def lookup(self, mac: str | None) -> str | None:
        if not mac:
            return None
        parts = normalize_mac(mac).split(":")
        for length in (3, 4, 2):
            if len(parts) < length:
                continue
            prefix = ":".join(parts[:length])
            vendor = self._mapping.get(prefix)
            if vendor:
                return vendor
        return None
