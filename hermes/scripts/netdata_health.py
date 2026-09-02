#!/usr/bin/env python3
"""Collect a Netdata health summary and forward it to Hermes as an event.

Runs hourly via cron. Posts a single `netdata/health` event to Hermes,
which routes it to the configured providers (e.g. Telegram).

Env:
  NETDATA_URL   base URL of the Netdata agent   (default http://127.0.0.1:19999)
  HERMES_URL    base URL of Hermes              (default http://127.0.0.1:8002)
"""

from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.request
from datetime import datetime

NETDATA_URL = os.environ.get("NETDATA_URL", "http://127.0.0.1:19999").rstrip("/")
HERMES_URL = os.environ.get("HERMES_URL", "http://127.0.0.1:8002").rstrip("/")


def fetch(path: str) -> dict | list:
    url = f"{NETDATA_URL}{path}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.load(resp)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"failed to fetch {url}: {exc}") from exc


def _point(chart: str) -> tuple[list[str], list]:
    # Chart ids may contain "/" (e.g. disk_space./); netdata expects it raw,
    # so the id is interpolated without percent-encoding.
    data = fetch(f"/api/v1/data?chart={chart}&after=-60&before=0&points=1")
    labels = data["labels"]
    row = data["data"][0][1:]  # drop the timestamp column
    return labels, row


def _sum_fields(labels: list[str], row: list, names: set[str]) -> float:
    # data row has no timestamp, so align against labels[1:].
    return sum(
        v
        for lbl, v in zip(labels[1:], row, strict=False)
        if lbl in names and isinstance(v, (int, float))
    )


def collect() -> dict:
    info = fetch("/api/v1/info")
    alarms = info.get("alarms", {})
    labels, row = _point("system.cpu")
    cpu_used = _sum_fields(labels, row, set(labels))  # chart has no idle column
    labels, row = _point("system.ram")
    ram_total = _sum_fields(labels, row, {"used", "free", "cached", "buffers"})  # MiB
    ram_used = _sum_fields(labels, row, {"used"})  # MiB
    labels, row = _point("disk_space./")
    disk_total = _sum_fields(labels, row, {"used", "avail", "reserved for root"})  # GiB
    disk_used = _sum_fields(labels, row, {"used"})  # GiB
    return {
        "hostname": socket.gethostname(),
        "netdata_version": info.get("version", "unknown"),
        "cores": info.get("cores_total", "?"),
        "cpu_used": round(cpu_used),
        "ram_used_mib": ram_used,
        "ram_total_mib": ram_total,
        "disk_used_gib": disk_used,
        "disk_total_gib": disk_total,
        "alarms": alarms,
    }


def format_message(s: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cpu_pct = min(s["cpu_used"], 100)
    ram_pct = s["ram_used_mib"] / s["ram_total_mib"] * 100 if s["ram_total_mib"] else 0
    disk_pct = s["disk_used_gib"] / s["disk_total_gib"] * 100 if s["disk_total_gib"] else 0

    def gib(value: float) -> str:
        return f"{value:.1f} GiB"

    def gib_from_mib(value: float) -> str:
        return gib(value / 1024)

    warning = s["alarms"].get("warning", 0)
    critical = s["alarms"].get("critical", 0)
    if critical:
        alarm_line = f"🔴 {critical} critical"
        if warning:
            alarm_line += f", ⚠️ {warning} warning"
    elif warning:
        alarm_line = f"⚠️ {warning} warning"
    else:
        alarm_line = "🟢 all clear"
    return (
        f"🕐 {now} ({s['hostname']})\n"
        f"\n"
        f"CPU:  {cpu_pct}% (used, {s['cores']} cores)\n"
        f"RAM:  {gib_from_mib(s['ram_used_mib'])} / "
        f"{gib_from_mib(s['ram_total_mib'])} ({ram_pct:.0f}%)\n"
        f"Disk /: {gib(s['disk_used_gib'])} / {gib(s['disk_total_gib'])} ({disk_pct:.0f}%)\n"
        f"\n"
        f"Alerts: {alarm_line}\n"
        f"Netdata: {s['netdata_version']}"
    )


def post_event(title: str, message: str) -> None:
    payload = {
        "module": "netdata",
        "type": "health",
        "severity": "info",
        "title": title,
        "message": message,
        "tags": ["netdata", "healthbeat"],
    }
    req = urllib.request.Request(
        f"{HERMES_URL}/event",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.load(resp)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"failed to post event to Hermes: {exc}") from exc
    print(f"queued event {body.get('id')} ({body.get('status')})")


def main() -> int:
    try:
        summary = collect()
        post_event("Netdata health", format_message(summary))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
