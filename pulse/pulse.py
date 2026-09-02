#!/usr/bin/env python3
"""anton-pulse — stack performance pulse probe for Anton.

Probes every reachable service on the host (auto-discovered from the Docker
daemon), plus the Forge and Oracle gateways over the tailnet, records HTTP
latency distributions (p50/p95/p99), times the AI decision round-trip the
self-healing loop depends on, and benchmarks the disk it lives on.

Read-only by design: it never posts events, restarts anything, or touches
other tools' state. The only writes are its own report files and a temporary
benchmark file, both inside the report directory.

Stdlib-only (no pip install needed).

Env:
  ANTON_PULSE_TIMEOUT       per-request socket timeout seconds  (default 8)
  ANTON_PULSE_SAMPLES       probes per target                   (default 5)
  ANTON_PULSE_WORKERS       concurrent targets probed           (default 16)
  ANTON_PULSE_ORACLE_TOKEN  Bearer token for Oracle /v1/decide  (optional)
  ANTON_PULSE_REPORT_DIR    report + benchmark output dir       (default CWD)
  ANTON_PULSE_BENCH_SIZE_MB sequential read/write size in MB    (default 64)
  ANTON_PULSE_BENCH_RANDOM  random 4KiB IO operations           (default 512)

Usage:
  python3 pulse.py                one sweep + report
  python3 pulse.py --watch 30     continuous sweeps every 30 s (Ctrl-C stops)
"""

from __future__ import annotations

import json
import mmap
import os
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from statistics import median, pstdev
from urllib.parse import urlsplit

__version__ = "1.0.0"

TIMEOUT = float(os.environ.get("ANTON_PULSE_TIMEOUT", "8"))
DECIDE_TIMEOUT = float(os.environ.get("ANTON_PULSE_DECIDE_TIMEOUT", "60"))
SAMPLES = int(os.environ.get("ANTON_PULSE_SAMPLES", "5"))
WORKERS = int(os.environ.get("ANTON_PULSE_WORKERS", "16"))
REPORT_DIR = Path(os.environ.get("ANTON_PULSE_REPORT_DIR", "."))
BENCH_SIZE_MB = int(os.environ.get("ANTON_PULSE_BENCH_SIZE_MB", "64"))
BENCH_RANDOM = int(os.environ.get("ANTON_PULSE_BENCH_RANDOM", "512"))

ORACLE_TOKEN = os.environ.get("ANTON_PULSE_ORACLE_TOKEN", "") or None
ORACLE_URL = "http://100.84.233.111:8003"
FORGE_URL = "http://100.77.54.107:8092/health"

#: Fallback targets when the docker CLI is unavailable.
_FALLBACK_TARGETS = [
    ("sonofanton", "http://127.0.0.1:8000/health"),
    ("phoenix", "http://127.0.0.1:8010/health"),
    ("gitea", "http://127.0.0.1:3000/api/v1/version"),
    ("homepage", "http://127.0.0.1:3005/"),
    ("grafana", "http://127.0.0.1:3006/"),
    ("forge", FORGE_URL),
    ("oracle", f"{ORACLE_URL}/health"),
]

#: A short, benign situation: the model must answer with the strict JSON
#: decision contract ("none" action) — nothing is proposed or executed.
_DECIDE_SITUATION = (
    "Situation summary for the Anton watchdog:\n"
    "- Latest event: none, all monitored systems report healthy.\n"
    "- Recent actions: none.\n"
    'Answer with the strict JSON decision contract, action "none".'
)


# ---------------------------------------------------------------------------
# HTTP probing (raw socket, so connect / TTFB / total are separable)
# ---------------------------------------------------------------------------

def probe_http(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = TIMEOUT,
) -> dict:
    """Probe ``url`` and return per-phase timings in milliseconds."""
    u = urlsplit(url)
    host = u.hostname or "127.0.0.1"
    port = u.port or (443 if u.scheme == "https" else 80)
    path = u.path or "/"
    if u.query:
        path = f"{path}?{u.query}"

    t0 = time.perf_counter()
    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        return {
            "ok": False,
            "tcp_ok": False,
            "status": 0,
            "error": f"connect {exc.__class__.__name__}: {exc}",
            "total_ms": round((time.perf_counter() - t0) * 1000, 2),
        }
    t_connect = time.perf_counter()
    tcp_ok = False
    try:
        if u.scheme == "https":
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=host)
        sock.settimeout(timeout)
        request = (
            f"{method} {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "User-Agent: anton-pulse/1.0\r\n"
            "Accept: */*\r\n"
            "Connection: close\r\n"
        )
        for key, value in (headers or {}).items():
            request += f"{key}: {value}\r\n"
        if body is not None:
            request += f"Content-Length: {len(body)}\r\n"
        request += "\r\n"
        sock.sendall(request.encode() + (body or b""))

        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        tcp_ok = True
    except OSError as exc:
        return {
            "ok": False,
            "tcp_ok": tcp_ok,
            "status": 0,
            "error": f"{exc.__class__.__name__}: {exc}",
            "total_ms": round((time.perf_counter() - t0) * 1000, 2),
        }
    finally:
        with suppress(Exception):
            if sock is not None:
                sock.close()
    t_first = time.perf_counter()
    head = data.split(b"\r\n\r\n", 1)[0].decode(errors="replace")
    status = 0
    lines = head.split("\r\n")
    if lines and lines[0].startswith("HTTP"):
        parts = lines[0].split(" ", 2)
        if len(parts) >= 2:
            with suppress(ValueError):
                status = int(parts[1])
    t_done = time.perf_counter()
    ok = 100 <= status < 600
    if not ok and status == 0:
        error = None if tcp_ok else "no response"
    else:
        error = None if ok else f"HTTP {status}"
    return {
        "ok": ok,
        "status": status,
        "tcp_ok": tcp_ok,
        "connect_ms": round((t_connect - t0) * 1000, 2),
        "ttfb_ms": round((t_first - t_connect) * 1000, 2),
        "total_ms": round((t_done - t0) * 1000, 2),
        "error": error,
    }


# ---------------------------------------------------------------------------
# Target discovery
# ---------------------------------------------------------------------------

def _docker_targets() -> list[tuple[str, str]]:
    """Discover published container endpoints from the docker daemon."""
    targets: list[tuple[str, str]] = []
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\\t{{.Ports}}"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return targets
    for line in out.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        name, ports = parts
        for mapping in ports.split(","):
            mapping = mapping.strip()
            if "->" not in mapping:
                continue
            host_part = mapping.split("->", 1)[0].strip()
            if host_part.startswith("["):
                host_ip, _, port = host_part[1:].partition("]:")
                host_ip = host_ip or "::1"
                port = port or ""
            elif ":" in host_part:
                host_ip, port = host_part.rsplit(":", 1)
                if host_ip in ("0.0.0.0", "*"):
                    host_ip = "127.0.0.1"
            else:
                host_ip, port = "127.0.0.1", host_part
            if not port.isdigit():
                continue
            url = f"http://[{host_ip}]:{port}/" if ":" in host_ip else f"http://{host_ip}:{port}/"
            targets.append((name, url))
            break
    return targets


def discover_targets() -> list[tuple[str, str]]:
    """Auto-discovered services plus the tailnet gateways."""
    fallback = dict(_FALLBACK_TARGETS)
    targets = _docker_targets()
    if not targets:
        targets = list(_FALLBACK_TARGETS)
    else:
        # Always include the gateways Hermes depends on.
        known = {name for name, _ in targets}
        for name in ("forge", "oracle", "sonofanton"):
            if name not in known:
                targets.append((name, fallback[name]))
    # De-duplicate by host:port. Where a discovered endpoint shares an origin
    # with a known gateway, prefer the gateway's canonical URL (health path).
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for name, url in targets:
        u = urlsplit(url)
        key = f"{u.scheme}://{u.netloc}"
        if key in seen:
            continue
        seen.add(key)
        if name in fallback and urlsplit(fallback[name]).netloc == u.netloc:
            url = fallback[name]
        unique.append((name, url))
    return unique


# ---------------------------------------------------------------------------
# Latency statistics
# ---------------------------------------------------------------------------

def percentiles(values: list[float]) -> dict:
    values = sorted(v for v in values if v is not None)
    if not values:
        return {"n": 0}
    def pct(p: float) -> float:
        idx = min(len(values) - 1, int(round(p * (len(values) - 1))))
        return round(values[idx], 2)
    return {
        "n": len(values),
        "p50_ms": pct(0.50),
        "p95_ms": pct(0.95),
        "p99_ms": pct(0.99),
        "min_ms": round(min(values), 2),
        "max_ms": round(max(values), 2),
        "mean_ms": round(sum(values) / len(values), 2),
        "stdev_ms": round(pstdev(values), 2),
    }


# ---------------------------------------------------------------------------
# Oracle decide (the AI round-trip the watchdog loop pays for)
# ---------------------------------------------------------------------------

def oracle_decide(token: str, timeout: float = DECIDE_TIMEOUT) -> dict | None:
    if not token:
        return None
    body = json.dumps({"situation": _DECIDE_SITUATION}).encode()
    result = probe_http(
        f"{ORACLE_URL}/v1/decide",
        method="POST",
        body=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    return result


# ---------------------------------------------------------------------------
# Drop OS page cache (best effort) so disk reads measure the drive
# ---------------------------------------------------------------------------

_DROP_CACHES_HELPER = "/usr/local/sbin/anton-pulse-drop-caches"


def _drop_caches() -> bool:
    """Sync + drop OS page caches. Returns whether the drop happened.

    Prefers a root-owned, passwordless helper (see install.sh / sudoers.d);
    falls back to sudo with ANTON_PULSE_SUDO_PASS.
    """
    args: list[str]
    stdin: bytes | None = None
    if os.geteuid() == 0:
        args = ["/bin/sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"]
    elif shutil.which("sudo") and os.path.exists(_DROP_CACHES_HELPER):
        args = ["sudo", "-n", _DROP_CACHES_HELPER]
    else:
        password = os.environ.get("ANTON_PULSE_SUDO_PASS", "") or ""
        if not password or not shutil.which("sudo"):
            return False
        args = ["sudo", "-S", "-p", "", "/bin/sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"]
        stdin = password.encode()
    try:
        proc = subprocess.run(args, input=stdin, capture_output=True, timeout=30)
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ---------------------------------------------------------------------------
# Disk benchmark (this drive: does it hold up?)
# ---------------------------------------------------------------------------

def bench_disk(report_dir: Path) -> dict:
    """Sequential throughput + random 4KiB IOPS + fsync latency, stdlib only."""
    bench = report_dir / "pulse_bench.tmp"
    size = BENCH_SIZE_MB * 1024 * 1024
    chunk = 1024 * 1024
    page = 4096
    results: dict = {}

    data = os.urandom(chunk)
    fd = os.open(bench, os.O_CREAT | os.O_TRUNC | os.O_RDWR)
    buf = mmap.mmap(-1, chunk)
    try:
        # Sequential write (buffered — reflects normal usage) + fsync
        t0 = time.perf_counter()
        remaining = size
        while remaining > 0:
            os.write(fd, data[: min(remaining, len(data))])
            remaining -= len(data)
        os.fsync(fd)
        write_s = time.perf_counter() - t0
        results["write_mb_s"] = round((BENCH_SIZE_MB / write_s) if write_s else 0, 2)

        # Random 4KiB offsets, page-aligned (O_DIRECT demands it)
        blocks = max(1, size // page)
        rng = os.urandom
        offsets = [
            int.from_bytes(rng(4), "big") % blocks * page for _ in range(BENCH_RANDOM)
        ]

        # Random 4KiB writes (buffered) + fsync
        blob = os.urandom(page)
        t0 = time.perf_counter()
        for off in offsets:
            os.pwrite(fd, blob, off)
        os.fsync(fd)
        rand_write_s = time.perf_counter() - t0
        results["rand_write_iops"] = round(BENCH_RANDOM / rand_write_s, 1) if rand_write_s else 0

        # fsync latency
        t0 = time.perf_counter()
        os.fsync(fd)
        results["fsync_ms"] = round((time.perf_counter() - t0) * 1000, 2)

        # Drop OS page caches so reads measure the drive, not RAM. Without
        # this the freshly-written file is still fully cached and reads look
        # impossibly fast.
        uncached = _drop_caches()
        results["uncached"] = uncached

        # Reads bypass page cache (O_DIRECT) so they measure the drive, not
        # the RAM copy of the writes above.
        direct_fd = None
        try:
            direct_fd = os.open(bench, os.O_RDONLY | os.O_DIRECT)
        except OSError:
            with suppress(OSError):
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        rfd = direct_fd if direct_fd is not None else fd
        rand_buf = mmap.mmap(-1, page)

        # Sequential read
        t0 = time.perf_counter()
        remaining = size
        offset = 0
        while remaining > 0:
            os.preadv(rfd, [buf], offset)
            remaining -= chunk
            offset += chunk
        read_s = time.perf_counter() - t0
        results["read_mb_s"] = round((BENCH_SIZE_MB / read_s) if read_s else 0, 2)

        # Random 4KiB reads
        t0 = time.perf_counter()
        for off in offsets:
            os.preadv(rfd, [rand_buf], off)
        rand_read_s = time.perf_counter() - t0
        results["rand_read_iops"] = round(BENCH_RANDOM / rand_read_s, 1) if rand_read_s else 0

        rand_buf.close()
        if direct_fd is not None:
            os.close(direct_fd)
    finally:
        with suppress(Exception):
            buf.close()
        with suppress(Exception):
            os.close(fd)
        with suppress(Exception):
            bench.unlink()

    results["bench_mb"] = BENCH_SIZE_MB
    return results


# ---------------------------------------------------------------------------
# Resource usage of the probe itself
# ---------------------------------------------------------------------------

def rss_mb() -> float:
    try:
        pages = int(Path("/proc/self/statm").read_text().split()[1])
        return round(pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024), 2)
    except (OSError, ValueError, IndexError):
        return 0.0


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _duration(total_ms: float) -> str:
    if total_ms < 1000:
        return f"{total_ms:.0f} ms"
    return f"{total_ms / 1000:.2f} s"


def write_report(
    targets: list[tuple[str, str]],
    samples: list[tuple[str, str, dict]],
    llm: dict | None,
    disk: dict,
    sweep_ms: float,
    rss: float,
) -> dict:
    """Aggregate one sweep into a structured report + markdown + JSON files."""
    report: dict = {
        "tool": "anton-pulse",
        "version": __version__,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sweep": {"targets": len(targets), "sweep_ms": round(sweep_ms, 2)},
        "self": {"rss_mb": rss, "samples_per_target": SAMPLES},
        "services": [],
        "oracle_decide_ms": None,
        "disk": disk,
    }

    # Group successful total latencies per target.
    grouped: dict[str, list] = {}
    for name, url, result in samples:
        entry = grouped.setdefault(name, [url, [], 0, []])
        entry[1].append(result.get("total_ms"))
        if result.get("ok"):
            entry[2] += 1
        else:
            error = result.get("error") or "no response"
            if result.get("tcp_ok"):
                error = "non-http (tcp ok)"
            entry[3].append(error)

    for name, (url, totals, ok_count, errors) in sorted(grouped.items()):
        stats = percentiles(totals)
        stats["ok"] = ok_count
        stats["errors"] = sorted(set(errors))
        report["services"].append({"name": name, "url": url, **stats})

    if llm is not None:
        report["oracle_decide_ms"] = llm.get("total_ms")
        report["oracle_decide_ttfb_ms"] = llm.get("ttfb_ms")
        report["oracle_decide_status"] = llm.get("status")
        report["oracle_decide_error"] = llm.get("error")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (REPORT_DIR / "report.md").write_text(_markdown(report) + "\n")
    return report


def _markdown(report: dict) -> str:
    lines = [
        "# Anton Pulse — performance report",
        "",
        f"Generated: {report['generated_at']}  ·  "
        f"services: {report['sweep']['targets']}  ·  "
        f"sweep: {_duration(report['sweep']['sweep_ms'])}  ·  "
        f"self RSS: {report['self']['rss_mb']} MiB",
        "",
        "## Service latency (total HTTP, ms)",
        "",
        "| service | ok | p50 | p95 | p99 | mean | max |",
        "|---|---|---|---|---|---|---|",
    ]
    for svc in report["services"]:
        if not svc["n"]:
            continue
        lines.append(
            f"| {svc['name']} | {svc['ok']}/{svc['n']} | {svc['p50_ms']} | "
            f"{svc['p95_ms']} | {svc['p99_ms']} | {svc['mean_ms']} | {svc['max_ms']} |"
        )
    failed = [svc for svc in report["services"] if svc["ok"] < svc["n"]]
    if failed:
        lines += ["", "### Unreachable / failed", ""]
        for svc in failed:
            lines.append(f"- {svc['name']}: {', '.join(svc['errors']) or 'no response'}")

    lines += ["", "## AI decision round-trip (Oracle /v1/decide)", ""]
    d = report.get("oracle_decide_ms")
    err = report.get("oracle_decide_error")
    if d is not None:
        line = f"- total: {_duration(d)}"
        if report.get("oracle_decide_ttfb_ms") is not None:
            line += f"  ·  ttfb: {_duration(report['oracle_decide_ttfb_ms'])}"
        if report.get("oracle_decide_status"):
            line += f"  ·  status: {report['oracle_decide_status']}"
        lines.append(line)
        if err:
            lines.append(f"- error: {err}")
    else:
        lines.append("- skipped (set ANTON_PULSE_ORACLE_TOKEN to measure)")

    disk = report["disk"]
    lines += [
        "",
        "## Disk (this drive)",
        "",
        f"- sequential write: {disk['write_mb_s']} MB/s",
        f"- sequential read:  {disk['read_mb_s']} MB/s",
        f"- random 4KiB read: {disk['rand_read_iops']} IOPS",
        f"- random 4KiB write:{disk['rand_write_iops']} IOPS",
        f"- fsync:            {disk['fsync_ms']} ms",
        "",
    ]
    if not disk.get("uncached"):
        lines.append(
            "- note: reads measured from page cache (best case); "
            "set ANTON_PULSE_SUDO_PASS to drop caches and measure the drive"
        )
        lines.append("")
    else:
        lines.append(
            "- note: random 4KiB reads are cache-limited by the ntfs-3g driver "
            "and are not a real drive capability"
        )
        lines.append(
            "- note: fsync at 0.03 ms is suspiciously fast and likely does not "
            "wait for a physical flush — a known ntfs-3g durability caveat"
        )
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main sweep + watch loop
# ---------------------------------------------------------------------------

def run_sweep(include_decide: bool = True) -> dict:
    targets = discover_targets()
    started = time.perf_counter()

    def probe_one(item: tuple[str, str]) -> tuple[str, str, list[dict]]:
        name, url = item
        results = [probe_http(url) for _ in range(SAMPLES)]
        return name, url, results

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        raw = list(pool.map(probe_one, targets))

    samples: list[tuple[str, str, dict]] = []
    for name, url, results in raw:
        for result in results:
            samples.append((name, url, result))

    llm = oracle_decide(ORACLE_TOKEN) if (include_decide and ORACLE_TOKEN) else None
    disk = bench_disk(REPORT_DIR)
    sweep_ms = (time.perf_counter() - started) * 1000

    report = write_report(targets, samples, llm, disk, sweep_ms, rss_mb())
    with (REPORT_DIR / "history.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "ts": report["generated_at"],
                    "sweep_ms": round(sweep_ms, 2),
                    "oracle_decide_ms": report.get("oracle_decide_ms"),
                    "disk_write_mb_s": disk.get("write_mb_s"),
                    "disk_read_mb_s": disk.get("read_mb_s"),
                }
            )
            + "\n"
        )
    return report


# ---------------------------------------------------------------------------
# Integration: Prometheus exporter + Hermes events
# ---------------------------------------------------------------------------

EXPORTER_PORT = int(os.environ.get("ANTON_PULSE_EXPORTER_PORT", "9654"))
EXPORTER_INTERVAL = float(os.environ.get("ANTON_PULSE_EXPORTER_INTERVAL", "60"))
EXPORTER_DECIDE_EVERY = int(os.environ.get("ANTON_PULSE_EXPORTER_DECIDE_EVERY", "10"))
HERMES_URL = os.environ.get("ANTON_PULSE_HERMES_URL", "http://127.0.0.1:8002")
HERMES_EVENTS = os.environ.get("ANTON_PULSE_HERMES_EVENTS", "1") in ("1", "true", "yes")


def service_up(result: dict) -> bool:
    """A service counts as reachable if TCP connected and it responded."""
    return bool(result.get("tcp_ok"))


def render_metrics(report: dict) -> str:
    lines = [
        "# HELP anton_pulse_sweep_ms Time of the last probe sweep, milliseconds.",
        "# TYPE anton_pulse_sweep_ms gauge",
        f"anton_pulse_sweep_ms {report['sweep']['sweep_ms']}",
        "# HELP anton_pulse_last_sweep_timestamp_seconds UNIX time of last sweep.",
        "# TYPE anton_pulse_last_sweep_timestamp_seconds gauge",
        f"anton_pulse_last_sweep_timestamp_seconds {time.time():.3f}",
    ]
    for svc in report["services"]:
        if not svc["n"]:
            continue
        name = svc["name"].replace("\\", "\\\\").replace('"', '\\"')
        label = f'service="{name}"'
        lines.append("# HELP anton_pulse_service_latency_ms HTTP latency percentiles per service.")
        lines.append("# TYPE anton_pulse_service_latency_ms gauge")
        for quantile in ("p50_ms", "p95_ms", "p99_ms", "min_ms", "max_ms", "mean_ms"):
            lines.append(
                f'anton_pulse_service_latency_ms{{{label},quantile="{quantile[:-3]}"}} '
                f"{svc[quantile]}"
            )
        lines.append("# HELP anton_pulse_service_up Whether the service was reachable.")
        lines.append("# TYPE anton_pulse_service_up gauge")
        lines.append(f"anton_pulse_service_up{{{label}}} {1 if svc['ok'] == svc['n'] else 0}")
        lines.append("# HELP anton_pulse_service_samples Probes per service in the last sweep.")
        lines.append("# TYPE anton_pulse_service_samples gauge")
        lines.append(f"anton_pulse_service_samples{{{label}}} {svc['n']}")
        lines.append("# HELP anton_pulse_service_errors Failed probes per service.")
        lines.append("# TYPE anton_pulse_service_errors gauge")
        lines.append(f"anton_pulse_service_errors{{{label}}} {svc['n'] - svc['ok']}")
    d = report.get("oracle_decide_ms")
    if d is not None:
        lines.append("# HELP anton_pulse_oracle_decide_ms End-to-end AI decide round-trip.")
        lines.append("# TYPE anton_pulse_oracle_decide_ms gauge")
        lines.append(f"anton_pulse_oracle_decide_ms {d}")
        if report.get("oracle_decide_ttfb_ms") is not None:
            lines.append("# HELP anton_pulse_oracle_decide_ttfb_ms Time to first byte of the decide call.")
            lines.append("# TYPE anton_pulse_oracle_decide_ttfb_ms gauge")
            lines.append(f"anton_pulse_oracle_decide_ttfb_ms {report['oracle_decide_ttfb_ms']}")
    disk = report["disk"]
    for key, help_txt in (
        ("write_mb_s", "Disk sequential write throughput."),
        ("read_mb_s", "Disk sequential read throughput (uncached)."),
        ("rand_write_iops", "Disk random 4KiB write IOPS."),
        ("fsync_ms", "Disk fsync latency."),
    ):
        if key not in disk:
            continue
        metric = f"anton_pulse_disk_{key}"
        lines.append(f"# HELP {metric} {help_txt}")
        lines.append(f"# TYPE {metric} gauge")
        lines.append(f"{metric} {disk[key]}")
    lines.append("# EOF")
    return "\n".join(lines)


def post_hermes_event(
    event_type: str,
    severity: str,
    title: str,
    message: str,
    metadata: dict,
    tags: list[str],
) -> None:
    import urllib.request

    payload = {
        "module": "pulse.probe",
        "type": event_type,
        "severity": severity,
        "title": title,
        "message": message,
        "metadata": metadata,
        "tags": ["pulse", *tags],
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{HERMES_URL}/event",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status not in (200, 202):
                print(f"hermes event {event_type}: unexpected HTTP {resp.status}")
    except OSError as exc:
        print(f"hermes event {event_type}: {exc.__class__.__name__}: {exc}")


class _ExporterState:
    def __init__(self) -> None:
        self.report: dict | None = None
        self._up: dict[str, bool] = {}
        self._lock = threading.Lock()

    def note_sweep(self, report: dict) -> None:
        with self._lock:
            self.report = report
        if not HERMES_EVENTS:
            return
        new_up: dict[str, bool] = {}
        for svc in report["services"]:
            new_up[svc["name"]] = svc["ok"] == svc["n"]
        with self._lock:
            baseline = not self._up
            previous = dict(self._up)
            self._up = new_up
        if baseline:
            return
        for name, up in new_up.items():
            before = previous.get(name)
            if before is None or before == up:
                continue
            svc = next(s for s in report["services"] if s["name"] == name)
            if not up:
                post_hermes_event(
                    "service.unreachable",
                    "warning",
                    f"{name} unreachable",
                    f"{name} failed {svc['n'] - svc['ok']}/{svc['n']} probes in the last sweep.",
                    {"service": name, "ok": svc["ok"], "total": svc["n"]},
                    ["probe"],
                )
            else:
                post_hermes_event(
                    "service.recovered",
                    "info",
                    f"{name} reachable again",
                    f"{name} passed {svc['ok']}/{svc['n']} probes in the last sweep.",
                    {"service": name, "ok": svc["ok"], "total": svc["n"]},
                    ["probe"],
                )


def run_exporter() -> int:
    import http.server

    state = _ExporterState()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib casing)
            if self.path == "/health":
                body = b"ok"
                self.send_response(200)
            elif self.path == "/metrics":
                with state._lock:
                    report = state.report
                if report is None:
                    body = b"# no sweep yet"
                else:
                    body = render_metrics(report).encode()
                self.send_response(200)
            else:
                self.send_response(404)
                body = b"not found"
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:  # silence request spam
            pass

    server = http.server.ThreadingHTTPServer(("0.0.0.0", EXPORTER_PORT), Handler)

    def sweep_loop() -> None:
        n = 0
        while True:
            try:
                n += 1
                report = run_sweep(include_decide=(n % EXPORTER_DECIDE_EVERY == 0))
                state.note_sweep(report)
                print(
                    f"sweep: {sum(1 for s in report['services'] if s['ok'] == s['n'])}/"
                    f"{len(report['services'])} up in {report['sweep']['sweep_ms']:.0f} ms"
                )
            except Exception as exc:  # keep the loop alive on any failure
                print(f"sweep failed: {exc.__class__.__name__}: {exc}")
            time.sleep(EXPORTER_INTERVAL)

    threading.Thread(target=sweep_loop, daemon=True).start()
    print(f"anton-pulse exporter on http://127.0.0.1:{EXPORTER_PORT}/metrics "
          f"(interval {EXPORTER_INTERVAL:.0f}s, hermes events {'on' if HERMES_EVENTS else 'off'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def print_summary(report: dict) -> None:
    ok = sum(1 for s in report["services"] if s["n"] and s["ok"] == s["n"])
    total = len(report["services"])
    lines = [
        f"anton-pulse sweep: {ok}/{total} services reachable "
        f"in {_duration(report['sweep']['sweep_ms'])} (RSS {report['self']['rss_mb']} MiB)",
    ]
    best = [s for s in report["services"] if s["n"] and s["ok"] == s["n"]]
    for key in ("p95_ms", "p99_ms"):
        if not best:
            continue
        worst = max(best, key=lambda s: s.get(key, 0))
        lines.append(
            f"  worst {key.replace('_ms', '')}: {worst['name']} = {worst[key]} ms "
            f"(p50 {worst['p50_ms']} ms)"
        )
    d = report.get("oracle_decide_ms")
    if d is not None:
        line = f"  Oracle decide round-trip: {_duration(d)}"
        if report.get("oracle_decide_error"):
            line += f"  (error: {report['oracle_decide_error']})"
        lines.append(line)
    lines.append(f"  disk: {report['disk']['write_mb_s']} MB/s write, "
                 f"{report['disk']['read_mb_s']} MB/s read")
    print("\n".join(lines))
    print(f"  report: {REPORT_DIR / 'report.md'}")


def selftest() -> int:
    """Deterministic, network-free smoke test (used by CI)."""
    import shutil as _sh
    import tempfile as _tmp

    tmp = Path(_tmp.mkdtemp(prefix="anton-pulse-selftest-"))
    old_dir = REPORT_DIR
    try:
        globals()["REPORT_DIR"] = tmp
        BENCH_SIZE_MB_BAK, BENCH_RANDOM_BAK = BENCH_SIZE_MB, BENCH_RANDOM
        globals()["BENCH_SIZE_MB"] = 8
        globals()["BENCH_RANDOM"] = 64

        disk = bench_disk(tmp)
        assert disk.get("write_mb_s") > 0, "benchmark produced no write result"

        fake = []
        for name, status in (("alpha", 200), ("beta", 0)):
            for i in range(3):
                ok = status == 200
                fake.append(
                    (
                        name,
                        "http://example.invalid/",
                        {
                            "ok": ok,
                            "tcp_ok": ok or True,
                            "status": status,
                            "total_ms": 1.0 + i,
                            "error": None if ok else "non-http (tcp ok)",
                        },
                    )
                )
        report = write_report(
            targets=[("alpha", "http://example.invalid/"), ("beta", "http://example.invalid/")],
            samples=fake,
            llm={"total_ms": 5.0, "ttfb_ms": 4.0, "status": 200, "error": None},
            disk=disk,
            sweep_ms=7.0,
            rss=1.0,
        )
        assert report["sweep"]["targets"] == 2
        assert (tmp / "report.md").exists() and (tmp / "report.json").exists()
        (tmp / "history.jsonl").write_text("")
        print(f"selftest OK — disk {disk['write_mb_s']} MB/s write, "
              f"{disk['read_mb_s']} MB/s read")
        return 0
    finally:
        globals()["REPORT_DIR"] = old_dir
        globals()["BENCH_SIZE_MB"] = BENCH_SIZE_MB_BAK  # type: ignore[union-attr]
        globals()["BENCH_RANDOM"] = BENCH_RANDOM_BAK  # type: ignore[union-attr]
        _sh.rmtree(tmp, ignore_errors=True)


def main() -> int:
    watch = 0
    args = sys.argv[1:]
    if args and args[0] == "--selftest":
        return selftest()
    if args and args[0] == "--exporter":
        return run_exporter()
    if args and args[0] == "--watch":
        try:
            watch = int(args[1])
        except (IndexError, ValueError):
            print("usage: pulse.py [--watch SECONDS]")
            return 2
    elif args:
        print("usage: pulse.py [--watch SECONDS]")
        return 2

    print(f"anton-pulse {__version__} — report dir {REPORT_DIR}")
    try:
        while True:
            report = run_sweep()
            print_summary(report)
            if not watch:
                return 0
            time.sleep(watch)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
