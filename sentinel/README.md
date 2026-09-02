# Anton Sentinel — perception and situational awareness subsystem.

Sentinel observes the Anton server and LAN (system load, docker fleet, router
reachability, watcher network data, UPS, HTTP probes, ARP presence) and turns
raw observations into correlated situations via a rule engine. It only
reports to Hermes; it never acts (that is Phoenix's job).

## Run

```bash
cp .env.example .env
docker compose up -d --build
```

The container uses `network_mode: host` so observers can reach the host's
loopback (Hermes `127.0.0.1:8002`, Watcher `127.0.0.1:8008`), read `/proc`
ARP/route tables and ping the gateway. Tune behavior in `app/config/*.yaml`
(observers, rules, devices, vendors) — the config directory is mounted
read-only, no rebuild needed.

## Layout

| Directory | Purpose |
|---|---|
| `app/observers` | sensors (system, docker, router, watcher, ups, http, network) |
| `app/correlation` | rule engine turning observations into situations |
| `app/presence` | device presence engine |
| `app/network` | device tracker + vendor lookup |
| `app/config` | YAML tuning (observers, rules, devices, vendors) |
| `app/api` | FastAPI (health, devices, observations, situations, presence) |

## Tests

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```
