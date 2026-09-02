# Operations

## Running locally

```bash
cp .env.example .env
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8010
```

The scheduler starts automatically unless `PHOENIX_SCHEDULER_ENABLED=false`
is set (tests use this).

## Running in Docker

```bash
docker compose up -d --build
```

Notes:

- The compose file mounts `/var/run/docker.sock` so Phoenix can restart the
  containers it watches. The container also needs `systemctl` on the host bus
  for the `systemd_restart` strategy (mount
  `/run/dbus/system_bus_socket`) if you use systemd units.
- The incident database persists in the `phoenix_data` volume
  (`PHOENIX_DATABASE_URL=sqlite+aiosqlite:////data/phoenix.db`).
- Hermes is expected on the same network; `PHOENIX_HERMES_BASE_URL` defaults
  to `http://hermes:8000` there. Start Hermes first.

## Health

`GET /health` reports:

- `status`: `ok` or `degraded` (DB unreachable, or scheduler enabled but not
  running)
- per-monitor last result (`ok`, `status`, `last_check`)
- open incident count and Hermes connectivity settings

## Manual recovery

`POST /recover/{component}` runs the full workflow (strategy, retries,
backoff, verification, dependency cascade) synchronously and returns the
final incident:

```bash
curl -X POST localhost:8010/recover/postgres
```

## Incidents

```bash
# all incidents, newest first
curl localhost:8010/incidents

# filter + paginate
curl 'localhost:8010/incidents?component=postgres&status=unresolved&limit=10'

# single incident with its recovery timeline
curl localhost:8010/incidents/<incident_id>
```

## Statistics

`GET /statistics` aggregates the incident history (failures per component,
recoveries per strategy, mean recovery time, open incidents). These numbers
feed later AI analysis on Oracle.

## Maintenance windows

Use the API (see `docs/configuration.md`) — failures inside a window are
recorded as `maintenance` and never trigger recovery.

## Troubleshooting

- **"docker daemon unreachable"** — the container cannot see
  `/var/run/docker.sock`, or the `docker` Python SDK cannot ping the daemon.
- **"systemctl is not available"** — the `systemd_restart` strategy requires
  `systemctl` in PATH (host mount) or the unit is not managed by the running
  systemd bus.
- **Hermes events missing** — check `PHOENIX_HERMES_BASE_URL` and that Hermes
  is up; publishing is best-effort and never fails the loop.
- **No incidents despite a down service** — confirm the component has a
  monitor enabled in the YAML and the scheduler is running
  (`GET /health`).
