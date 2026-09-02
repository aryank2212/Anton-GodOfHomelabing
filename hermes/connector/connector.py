#!/usr/bin/env python3
"""Connect everything to Hermes.

Two polling loops (one thread each):

* Fleet sweep   : docker container state transitions -> ``docker/container.*``
* Endpoint sweep: LAN + public (tunnel) endpoint health transitions -> ``infra/endpoint.*``

Only *transitions* are reported. The first sweep seeds the baseline and emits
nothing, so a freshly deployed connector does not spam Hermes with every
already-known state. State is persisted to ``/data/state.json`` so the
baseline survives restarts.

Env:
  CONNECTOR_HERMES_URL        Hermes base URL     (default http://hermes:8000)
  CONNECTOR_STATE_FILE        state file          (default /data/state.json)
  CONNECTOR_INTERVAL_FLEET    fleet poll seconds  (default 20)
  CONNECTOR_INTERVAL_ENDPOINTS endpoint poll secs (default 300)
  CONNECTOR_FLAP_COOLDOWN     min secs between events for same object+state
                              (default 90)
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone

import docker
import httpx

HERMES_URL = os.environ.get("CONNECTOR_HERMES_URL", "http://hermes:8000").rstrip("/")
STATE_FILE = os.environ.get("CONNECTOR_STATE_FILE", "/data/state.json")
FLEET_INTERVAL = float(os.environ.get("CONNECTOR_INTERVAL_FLEET", "20"))
ENDPOINT_INTERVAL = float(os.environ.get("CONNECTOR_INTERVAL_ENDPOINTS", "300"))
FLAP_COOLDOWN = float(os.environ.get("CONNECTOR_FLAP_COOLDOWN", "90"))
HERMES_TIMEOUT = 10.0

#: LAN service name -> host port (mirrors server/scripts/healthcheck.sh).
LAN_PORTS = {
    "homepage": 3005,
    "ops": 8091,
    "navidrome": 4533,
    "jellyfin": 8096,
    "immich": 2283,
    "gitea": 3000,
    "komga": 25600,
    "paperless": 8001,
    "stirling": 8088,
    "it-tools": 8089,
    "redclove-frontend": 3100,
    "redclove-api": 8100,
    "portfolio": 8082,
    "journal": 5050,
    "status": 3001,
    "gate": 8090,
    "dashboard-proxy": 3004,
    "passvault": 8095,
    "phoenix": 8010,
    "hermes": 8002,
    "sentinel": 8011,
    "watcher": 8008,
    "portainer": 9000,
    "grafana": 3006,
    "prometheus": 9090,
    "netdata": 19999,
    "cadvisor": 8083,
    "node-exporter": 9100,
    "npm": 81,
    "watchyourlan": 8840,
    "ntopng": 3007,
}

#: Services reachable only via loopback on the host get a dedicated probe URL.
LAN_URLS = {
    "hermes": "http://hermes:8000/health",
}

#: Public subdomains probed through the Cloudflare tunnel.
PUBLIC_SUBS = [
    "anton",
    "dashboard",
    "ops",
    "music",
    "media",
    "photos",
    "git",
    "manga",
    "notes",
    "tools",
    "utils",
    "site",
    "api",
    "portfolio",
    "journal",
    "status",
    "phoenix",
    "hermes",
    "sentinel",
    "watcher",
    "portainer",
    "grafana",
    "prometheus",
    "netdata",
    "cadvisor",
    "node-exporter",
    "npm",
    "watchyourlan",
    "ntopng",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    """Thread-safe JSON state persisted to disk."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {"fleet": {}, "endpoints": {}}
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path, encoding="utf-8") as fh:
                self._data = json.load(fh)
        except (OSError, ValueError):
            self._data = {"fleet": {}, "endpoints": {}}
        self._data.setdefault("fleet", {})
        self._data.setdefault("endpoints", {})

    def save(self) -> None:
        tmp = f"{self._path}.tmp"
        with self._lock:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh)
            os.replace(tmp, self._path)

    def snapshot(self, key: str) -> dict:
        with self._lock:
            return dict(self._data[key])

    def replace(self, key: str, value: dict) -> None:
        with self._lock:
            self._data[key] = value


def ok(code: int) -> bool:
    """Mirror healthcheck.sh: 000/5xx is a failure, everything else passes."""
    return not (code == 0 or code >= 500)


def post_event(
    client: httpx.Client,
    module: str,
    type_: str,
    severity: str,
    title: str,
    message: str = "",
    metadata: dict | None = None,
    tags: list[str] | None = None,
) -> bool:
    payload = {
        "module": module,
        "type": type_,
        "severity": severity,
        "title": title,
        "message": message,
        "metadata": metadata or {},
        "tags": tags or [],
    }
    try:
        resp = client.post(f"{HERMES_URL}/event", json=payload)
    except httpx.HTTPError as exc:
        print(f"{now_iso()} post failed for {module}/{type_}: {exc}", flush=True)
        return False
    if resp.status_code != 202:
        print(
            f"{now_iso()} hermes returned {resp.status_code} for {module}/{type_}",
            flush=True,
        )
        return False
    body = resp.json()
    print(
        f"{now_iso()} -> {module}/{type_} [{severity}] {title} (id {body.get('id')})",
        flush=True,
    )
    return True


def event_allowed(last: float) -> bool:
    return (time.monotonic() - last) >= FLAP_COOLDOWN


class FleetWatcher:
    """Poll docker container state and emit transition events."""

    def __init__(self, store: StateStore, client: httpx.Client) -> None:
        self._store = store
        self._client = client
        self._docker = docker.from_env()
        self._last_emit: dict[str, float] = {}

    def sweep(self) -> None:
        try:
            containers = self._docker.containers.list(all=True)
        except Exception as exc:  # noqa: BLE001 - daemon/socket down
            print(f"{now_iso()} docker unreachable: {exc}", flush=True)
            return

        state: dict[str, dict] = {}
        for c in containers:
            attrs = c.attrs or {}
            s = attrs.get("State", {}) or {}
            status = str(s.get("Status") or c.status or "unknown")
            health = (s.get("Health") or {}).get("Status")
            name = c.name
            if not name:
                continue
            if status == "running" and health in (None, "healthy", "starting"):
                perceived = "up"
            elif status == "running" and health == "unhealthy":
                perceived = "unhealthy"
            elif status == "restarting":
                perceived = "restarting"
            else:
                perceived = "down"
            state[name] = {
                "state": perceived,
                "health": health,
                "status": status,
                "restart_count": int(attrs.get("RestartCount", 0) or 0),
                "image": str((attrs.get("Config") or {}).get("Image") or ""),
                "exit_code": int((s.get("ExitCode") or 0) or 0),
                #: True when part of a docker compose project. One-shot job
                #: containers (e.g. Gitea Actions runners) have no project.
                "managed": bool(
                    ((attrs.get("Config") or {}).get("Labels") or {}).get(
                        "com.docker.compose.project"
                    )
                ),
            }

        previous = self._store.snapshot("fleet")
        seen: dict[str, dict] = {}
        for name, cur in state.items():
            prev = previous.get(name)
            if prev is None:
                seen[name] = cur  # first sighting: baseline, no event
                continue
            self._transition(name, prev, cur)
            seen[name] = cur
        for name, prev in previous.items():
            if name not in state and prev.get("state") != "down":
                if prev.get("managed") is False:
                    # One-shot job container outside any compose project (e.g.
                    # a Gitea Actions runner): it vanishes when the job ends.
                    # That is normal operation, not a fleet incident — skip it.
                    print(
                        f"{now_iso()} non-project container removed, skipping {name}",
                        flush=True,
                    )
                    continue
                self._emit(
                    "container.down",
                    "error",
                    f"Container disappeared: {name}",
                    meta={"previous": prev},
                    prev=prev,
                )
        self._store.replace("fleet", seen)
        self._store.save()

    def _transition(self, name: str, prev: dict, cur: dict) -> None:
        old = prev.get("state")
        new = cur["state"]
        if old == new:
            return
        meta = {
            "image": cur["image"],
            "health": cur["health"],
            "restart_count": cur["restart_count"],
            "exit_code": cur["exit_code"],
        }
        if new == "down":
            self._emit("container.down", "error", f"Container down: {name}", meta, prev)
        elif new == "unhealthy":
            self._emit("container.unhealthy", "error", f"Container unhealthy: {name}", meta, prev)
        elif new == "restarting":
            self._emit("container.restarting", "warning", f"Container restarting: {name}", meta, prev)
        elif old in ("down", "unhealthy", "restarting") and new == "up":
            self._emit("container.recovered", "info", f"Container recovered: {name}", meta, prev)

    def _emit(self, type_: str, severity: str, title: str, meta: dict, prev: dict) -> None:
        key = f"{type_}:{title}"
        last = self._last_emit.get(key, 0.0)
        if not event_allowed(last):
            print(f"{now_iso()} cooldown: skipping {title}", flush=True)
            return
        self._last_emit[key] = time.monotonic()
        message = f"was {prev.get('state')} ({prev.get('status') or '?'})"
        post_event(
            self._client,
            "docker",
            type_,
            severity,
            title,
            message=message,
            metadata=meta,
            tags=["docker", "container"],
        )


class EndpointWatcher:
    """Poll LAN + public endpoints and emit health transition events."""

    def __init__(self, store: StateStore, client: httpx.Client) -> None:
        self._store = store
        self._client = client
        self._last_emit: dict[str, float] = {}

    def targets(self) -> list[dict]:
        targets = [
            {
                "name": f"lan:{name}",
                "url": LAN_URLS.get(name) or f"http://host.docker.internal:{port}/",
            }
            for name, port in LAN_PORTS.items()
        ]
        targets.extend(
            {"name": f"public:{sub}", "url": f"https://{sub}.redclove.space/"}
            for sub in PUBLIC_SUBS
        )
        return targets

    def sweep(self) -> None:
        previous = self._store.snapshot("endpoints")
        seen: dict[str, dict] = {}
        for target in self.targets():
            name, url = target["name"], target["url"]
            code, ms = self._probe(url)
            cur = {"code": code, "ms": ms, "ok": ok(code)}
            prev = previous.get(name)
            if prev is not None:
                self._transition(name, prev, cur)
            seen[name] = cur
        self._store.replace("endpoints", seen)
        self._store.save()

    def _probe(self, url: str) -> tuple[int, int]:
        try:
            resp = self._client.get(url, timeout=10.0, follow_redirects=False)
            return resp.status_code, int(resp.elapsed.total_seconds() * 1000)
        except httpx.HTTPError:
            return 0, 0

    def _transition(self, name: str, prev: dict, cur: dict) -> None:
        if prev.get("ok") == cur["ok"]:
            return
        last = self._last_emit.get(name, 0.0)
        if not event_allowed(last):
            print(f"{now_iso()} cooldown: skipping {name}", flush=True)
            return
        self._last_emit[name] = time.monotonic()
        meta = {
            "url": name,
            "code": cur["code"],
            "latency_ms": cur["ms"],
            "previous_code": prev.get("code"),
        }
        if cur["ok"]:
            post_event(
                self._client,
                "infra",
                "endpoint.up",
                "info",
                f"Endpoint recovered: {name}",
                message=f"HTTP {cur['code']} in {cur['ms']}ms (was {prev.get('code')})",
                metadata=meta,
                tags=["infra", "endpoint"],
            )
        else:
            post_event(
                self._client,
                "infra",
                "endpoint.down",
                "error",
                f"Endpoint down: {name}",
                message=f"HTTP {cur['code']} (was {prev.get('code')})",
                metadata=meta,
                tags=["infra", "endpoint"],
            )


def fleet_loop(watcher: FleetWatcher, store: StateStore) -> None:
    while True:
        try:
            watcher.sweep()
        except Exception as exc:  # noqa: BLE001 - keep the thread alive
            print(f"{now_iso()} fleet sweep error: {exc}", flush=True)
        store.save()
        time.sleep(FLEET_INTERVAL)


def endpoint_loop(watcher: EndpointWatcher) -> None:
    while True:
        try:
            watcher.sweep()
        except Exception as exc:  # noqa: BLE001 - keep the thread alive
            print(f"{now_iso()} endpoint sweep error: {exc}", flush=True)
        time.sleep(ENDPOINT_INTERVAL)


def main() -> int:
    store = StateStore(STATE_FILE)
    with httpx.Client(timeout=HERMES_TIMEOUT) as client:
        fleet = FleetWatcher(store, client)
        endpoints = EndpointWatcher(store, client)
        threads = [
            threading.Thread(target=fleet_loop, args=(fleet, store), daemon=True, name="fleet"),
            threading.Thread(target=endpoint_loop, args=(endpoints,), daemon=True, name="endpoints"),
        ]
        print(
            f"{now_iso()} hermes-connector started (hermes={HERMES_URL}, "
            f"fleet={FLEET_INTERVAL}s, endpoints={ENDPOINT_INTERVAL}s)",
            flush=True,
        )
        for t in threads:
            t.start()
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    sys.exit(main())
