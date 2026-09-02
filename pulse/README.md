# Anton Pulse

A zero-dependency stack performance probe for Anton. It measures, in one
sweep, the latency of every service Hermes depends on, the AI decision
round-trip the self-healing loop pays for, and the disk it lives on — then
writes a human-readable report plus JSON to disk.

## Why it is new (does not overlap the existing tools)

- hermes-connector tracks *state transitions* and publishes events.
- netdata/cadvisor/prometheus collect *server metrics*.
- anton-pulse measures **request latency distributions per service**
  (connect/TTFB/total, p50/p95/p99), **end-to-end AI decide latency**, and
  **disk throughput**, and persists a rolling history on the HDD. It is
  read-only against every other tool: it never posts events, never restarts
  anything, and only writes its own files.

## Run

```bash
cd /media/ak/Drive/anton-pulse
python3 pulse.py                # one sweep + report.md / report.json / history.jsonl
python3 pulse.py --watch 30     # continuous sweeps every 30 s (Ctrl-C to stop)
python3 pulse.py --exporter     # Prometheus metrics + Hermes events, continuous
python3 pulse.py --selftest     # deterministic smoke test (used by CI)
ANTON_PULSE_ORACLE_TOKEN=... python3 pulse.py   # also time /v1/decide
```

No `pip install` — standard library only.

## Config (env)

| var | default | meaning |
|---|---|---|
| `ANTON_PULSE_TIMEOUT` | `8` | per-request socket timeout (s) |
| `ANTON_PULSE_SAMPLES` | `5` | probes per target |
| `ANTON_PULSE_WORKERS` | `16` | concurrent targets |
| `ANTON_PULSE_ORACLE_TOKEN` | — | Bearer token to time Oracle `/v1/decide` |
| `ANTON_PULSE_REPORT_DIR` | `.` | output directory |
| `ANTON_PULSE_BENCH_SIZE_MB` | `64` | disk sequential benchmark size |
| `ANTON_PULSE_BENCH_RANDOM` | `512` | random 4KiB IO operations |
| `ANTON_PULSE_EXPORTER_PORT` | `9654` | exporter HTTP port |
| `ANTON_PULSE_EXPORTER_INTERVAL` | `60` | exporter sweep interval (s) |
| `ANTON_PULSE_EXPORTER_DECIDE_EVERY` | `10` | run Oracle decide every N sweeps |
| `ANTON_PULSE_HERMES_URL` | `http://127.0.0.1:8002` | Hermes event API |
| `ANTON_PULSE_HERMES_EVENTS` | `1` | post up/down transitions to Hermes |

## Integration with other services

- **Prometheus** — `--exporter` serves `anton_pulse_*` metrics on
  `0.0.0.0:9654/metrics` (per-service latency percentiles, up/samples/errors,
  Oracle decide round-trip, disk throughput). Scrape job `anton_pulse` is in
  `/home/ak/docker/monitoring/prometheus.yml`.
- **Grafana** — dashboard "Anton Pulse" (uid `anton-pulse`) is provisioned
  from `/home/ak/docker/monitoring/grafana-provisioning/dashboards/`.
- **Hermes** — when a service flips reachable↔unreachable between sweeps, the
  exporter posts `pulse.probe` events (`service.unreachable` /
  `service.recovered`) to Hermes so its rules/notifications fire. The first
  sweep only establishes a baseline (no false alerts on restart).

## systemd

- `anton-pulse.timer` — full sweep (incl. Oracle decide) every 30 min, appends
  `history.jsonl`.
- `anton-pulse-exporter.service` — always-on exporter (Prometheus + Hermes
  events). Installed by `./install.sh`.

## Output

- `report.md` — human-readable performance report.
- `report.json` — full structured data (per-service percentiles, AI latency,
  disk numbers, probe RSS/sweep time).
- `history.jsonl` — one line per sweep for trend analysis.
