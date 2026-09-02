# Configuration

Phoenix separates concerns:

- **Process settings** (ports, database URL, Hermes URL, scheduler tuning) —
  environment variables with the `PHOENIX_` prefix, see `.env.example`.
- **System description** (what to monitor, how to recover, dependency graph) —
  the YAML file referenced by `PHOENIX_CONFIG_FILE`
  (default `app/config/phoenix.yaml`).

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PHOENIX_ENVIRONMENT` | `development` | runtime environment label |
| `PHOENIX_LOG_LEVEL` | `INFO` | log verbosity (structured JSON) |
| `PHOENIX_CONFIG_FILE` | `app/config/phoenix.yaml` | YAML system description |
| `PHOENIX_DATABASE_URL` | `sqlite+aiosqlite:///./data/phoenix.db` | incident store |
| `PHOENIX_API_HOST` | `0.0.0.0` | API bind host |
| `PHOENIX_API_PORT` | `8010` | API bind port |
| `PHOENIX_SCHEDULER_TICK_INTERVAL` | `10` | master loop interval (seconds) |
| `PHOENIX_SCHEDULER_MAX_CONCURRENT_CHECKS` | `8` | parallel monitor limit |
| `PHOENIX_HERMES_ENABLED` | `true` | publish events to Hermes |
| `PHOENIX_HERMES_BASE_URL` | `http://127.0.0.1:8000` | Hermes API base URL |
| `PHOENIX_HERMES_TIMEOUT` | `5` | Hermes request timeout (seconds) |
| `PHOENIX_HERMES_RETRY_ATTEMPTS` | `2` | retries after the first attempt |
| `PHOENIX_HERMES_RETRY_BACKOFF` | `1.0` | initial backoff (seconds) |

## YAML system description

### Monitors

A monitor is a single health check owned by exactly one component.

```yaml
monitors:
  - name: postgres_container
    type: docker
    interval: 30
    enabled: true
    params:
      container: postgres
      expected_status: running
```

Monitor types and their params:

| Type | Params |
|---|---|
| `docker` | `container` (req), `expected_status` (`running` \| `healthy`) |
| `systemd` | `unit` (req), `expected_state` (`active` default) |
| `http` | `url` (req), `method`, `expected_status`, `expected_body`, `timeout` |
| `network` | `host` (req), `port`, `timeout` |
| `disk` | `path`, `threshold_pct` |
| `memory` | `threshold_pct` |
| `cpu` | `threshold_pct`, `interval` (sampling) |

### Components

A component is a logical service of Anton. It groups monitors and declares
its recovery policy.

```yaml
components:
  - name: postgres
    monitors: [postgres_container]
    depends_on: [docker]           # restart dependents when this component recovers
    recovery:
      strategy: docker_restart      # docker_restart | systemd_restart | http | noop | dependency_restart
      params:
        container: postgres
      verify: true                  # require the monitor to pass before resolving
      retry:
        attempts: 3
        backoff: 5                  # seconds
        multiplier: 2               # backoff *= 2 between attempts
        max_backoff: 60             # cap the exponential backoff
      escalate:
        severity: critical          # severity reported on exhaustion
```

Recovery strategies:

| Strategy | Params | Action |
|---|---|---|
| `docker_restart` | `container`, `wait_seconds` | restart container via Docker daemon |
| `systemd_restart` | `unit` | `systemctl restart <unit>` |
| `http` | `url`, `method` | probe the endpoint to recover |
| `noop` | — | report-only: never changes anything |
| `dependency_restart` | `component` | bounce every service depending on the component |

**Note:** `dependency_restart` is available as an extension point but is not
used by the default configuration. The orchestrator's dependency cascade
(`_restart_dependents`) already restarts dependents after a successful
recovery using each dependent's own strategy.

### Maintenance

While a component is in an open maintenance window, its failures are recorded
as `status=maintenance` and recovery is skipped. Windows are managed via the
API:

```bash
curl -X POST localhost:8010/maintenance \
  -H 'content-type: application/json' \
  -d '{"component":"postgres","reason":"version upgrade","end_time":"2026-08-01T18:00:00Z"}'
```

## Validating the configuration

Phoenix validates the YAML on startup: every monitor referenced by a
component must exist, every dependency must name a defined component, and
recovery strategies must be registered. A malformed file prevents the app
from starting rather than failing at runtime.
