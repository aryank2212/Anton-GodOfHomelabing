# Architecture

Phoenix is the autonomous recovery/self-healing subsystem of Anton. It lives
on the Anton server (`/opt/anton/phoenix`) and runs **without any AI** —
every decision is rule-driven and deterministic. Pattern analysis of incident
history is a later subsystem (Oracle).

## The Observe → Diagnose → Recover → Learn → Report loop

```
                 ┌────────────────────────────────────────────────┐
                 │                Phoenix scheduler               │
                 │        (every tick_interval seconds)           │
                 └───────────────────────┬────────────────────────┘
                                         │ runs due monitors
                                         ▼
                                 ┌───────────────┐
                                 │    Monitors   │   OBSERVE — sensors only
                                 │  (7 types)    │
                                 └───────┬───────┘
                                         │ MonitorResult
                      ┌──────────────────┴──────────────────┐
                      │ healthy                            failing
                      ▼                                    ▼
              ┌───────────────┐                  ┌──────────────────┐
              │ handle_recovery │                │ handle_failure   │  DIAGNOSE
              │ closes open    │                │ opens incident   │
              │ incident       │                └────────┬─────────┘
              └───────────────┘                           ▼
                                             ┌──────────────────────┐
                                             │      Orchestrator     │  RECOVER
                                             │ retry/backoff engine │
                                             │ recovery strategy     │
                                             │ verify → escalate     │
                                             │ dependency cascade    │
                                             └──────────┬───────────┘
                                                        ▼
                                             ┌──────────────────────┐
                                             │   Incident history   │  LEARN
                                             │   /statistics        │  (+ Hermes events)
                                             └──────────────────────┘
```

### 1. Observe — monitors are sensors

A monitor implements a single health check and returns a `MonitorResult`
(`healthy` / `failing` with a status and diagnostic metrics). It has **no
power** to change anything. This keeps monitoring deterministic and safe.

Monitor types (`app/monitors/`):

| Kind | What it checks |
|---|---|
| `docker` | container exists and is running / healthy |
| `systemd` | unit is active |
| `http` | endpoint returns expected status / body |
| `network` | TCP reachability of a host:port |
| `disk` | filesystem usage vs threshold |
| `memory` | host memory usage vs threshold |
| `cpu` | host CPU usage vs threshold |

Monitors of the same name never run concurrently; failures can never crash
the loop (`safe_check` converts every exception into a failing result).

### 2. Diagnose — every failure is an incident

Failures are deduplicated: while an incident for a `(component, failure_type)`
is open/recovering/unresolved, repeat failures are ignored. Every incident is
persisted with its component, failure type, severity, detected-by source and
metadata.

Maintenance windows are honoured: failures observed inside an open window are
recorded as `status=maintenance` and recovery is skipped.

### 3. Recover — the recovery engine

Each component declares a recovery policy in the YAML config:

- **strategy** — `docker_restart`, `systemd_restart`, `http` (probe),
  `noop`, `dependency_restart`. Strategies are *pure actions*: no retry, no
  DB access, no event publishing. The retry engine wraps them.
- **retry** — attempts, initial backoff, multiplier, max backoff.
- **verify** — whether the monitor must pass before the incident is resolved.
- **escalate** — severity used when attempts are exhausted.

The recovery workflow:

1. Mark the incident `recovering`.
2. Execute the strategy with retry/backoff.
3. Verify (optionally) that the triggering monitor now passes.
4. Resolve the incident with strategy + attempts + duration, or escalate.

### 4. Learn — persistence and statistics

The incident record is the learning artifact: `recovery_strategy`,
`recovery_result`, `attempts`, `duration`, and a full event timeline. The
`/statistics` endpoint aggregates this history (failures per component,
recoveries per strategy, mean recovery time, …) for later AI analysis.

### 5. Report — Hermes

Phoenix never contacts notification providers. It publishes standardized
events to Hermes (`POST /event`): `incident_opened`, `recovery_success`,
`recovery_failed`, `recovery_escalated`. Publishing is best-effort with
retry; a down Hermes never blocks the monitoring loop.

## Process topology

```
Monitors ──► Scheduler ──► Orchestrator ──► IncidentService ──► Repository ──► SQLite
   │                          │  │  │
   │                          │  │  └─► HermesPublisher ──► Hermes /event
   │                          │  └────► RecoveryRegistry ──► Strategies ──► Docker/systemd
   │                          └───────► DependencyGraph ──► dependents
   │
   └─► HealthSnapshot (latest state, served by /health)
```

Everything is constructed in `app/main.py` `create_app()` and wired through a
lifespan handler; tests build the same stack with fake clients
(`tests/helpers.py`).

## Design invariants

- No AI code anywhere in Phoenix.
- Monitors never recover; strategies never sense.
- A failing Hermes / Docker / systemd never crashes the scheduler.
- All state is durable in SQLite; the YAML file is the single source of truth
  for what to watch and how to react.
