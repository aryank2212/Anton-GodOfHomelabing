# Phoenix — Anton's autonomous recovery subsystem

Phoenix is the self-healing subsystem of **Anton**. It continuously observes
the services on the Anton server, diagnoses failures, attempts automated
recovery, learns from the outcome, and reports every incident — including to
[Hermes](../hermes), Anton's event/notification hub.

Phoenix is a rule-based system: **there is no AI anywhere in it.** Artificial
intelligence (pattern analysis of the incident history) lives on Oracle, a
separate, later subsystem. Phoenix stays deterministic and auditable.

## How it works

Phoenix runs a loop (Observe → Diagnose → Recover → Learn → Report):

1. **Observe** — A scheduler ticks every few seconds and runs each enabled
   monitor when its interval is due. Monitors are *sensors only*: Docker
   container state, systemd units, HTTP endpoints, disk/memory/CPU usage and
   network reachability. A monitor never acts, it only reports.
2. **Diagnose** — Every failure becomes an **incident** (persisted). The
   failure is attributed to a *component*, and incidents for the same
   component/failure are deduplicated while one is still open.
3. **Recover** — For each component a recovery strategy is configured
   (`docker_restart`, `systemd_restart`, `http` probe, `noop`, …). Recovery
   runs with a retry/backoff policy, verifies that the monitor now passes, and
   then bounces dependents (dependency cascade). Failures that exhaust their
   attempts are escalated (a critical Hermes event).
4. **Learn** — The outcome is stored: recovery strategy, attempts, duration,
   whether it succeeded. `/statistics` exposes aggregated numbers for later AI
   analysis on Oracle.
5. **Report** — Every step is logged as structured JSON and published to
   Hermes (`recovery_success`, `recovery_failed`, `recovery_escalated`,
   `incident_opened`). Hermes owns all notifications.

## Architecture

| Directory | Purpose |
|---|---|
| `app/core` | logging, retry engine, client wrappers, scheduler |
| `app/config` | pydantic settings + YAML-driven monitoring/recovery config |
| `app/monitors` | health-check sensors (7 types) |
| `app/recovery` | recovery strategies (5 types) |
| `app/services` | dependency graph, orchestrator, incidents, statistics, maintenance, snapshot, Hermes publisher |
| `app/database` | SQLAlchemy async persistence (SQLite) |
| `app/api` | FastAPI application |

See [`docs/architecture.md`](docs/architecture.md) for the full picture.

## Quickstart

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env

# run the test suite
.venv/bin/python -m pytest

# run Phoenix (YAML config drives monitoring/recovery)
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8010
```

All configuration is environment-variable driven with the `PHOENIX_` prefix;
see [`docs/configuration.md`](docs/configuration.md).

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Phoenix liveness: DB, scheduler, monitor snapshot, open incidents |
| `GET` | `/incidents` | Incident history with filters + pagination |
| `GET` | `/incidents/{id}` | Single incident incl. recovery timeline |
| `GET` | `/statistics` | Aggregated recovery statistics |
| `POST` | `/recover/{component}` | Manually run a component's recovery workflow |
| `POST` | `/maintenance` | Open a maintenance window |
| `GET` | `/maintenance` | Maintenance log |
| `DELETE` | `/maintenance/{id}` | Close a maintenance window |

## Docker

```bash
docker compose up -d --build
```

The container mounts the Docker socket so Phoenix can restart the containers
it watches. Hermes is expected on the same network (`PHOENIX_HERMES_BASE_URL`
defaults to `http://hermes:8000` in the compose file). See
[`docs/operations.md`](docs/operations.md).

## Development

```bash
.venv/bin/ruff check app tests
.venv/bin/black --check app tests
.venv/bin/mypy app tests
.venv/bin/python -m pytest
```
