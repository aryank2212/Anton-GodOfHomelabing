# Anton Hermes

Hermes is the **communication and event system** of [Anton](https://github.com/anomalyco/anton) (the ANTON project). It is the **only** service in the stack that talks to notification providers (Discord, Telegram, Email, ntfy, generic webhooks).

Every other Anton module (Watcher, Phoenix, Legacy, Guardian, future modules) publishes **standardized events** to Hermes over HTTP. Hermes decides:

- whether an event should be logged,
- whether it should notify someone,
- **where** it should notify (which provider),
- **how** it should be formatted (per-provider templates),
- whether it should **act** (remediation: restart a container, hit an API, run a command),
- whether a burst of events is really an **event storm** that should be collapsed.

Hermes also **receives**: an optional inbound Telegram bot lets operators query Hermes (`/status`, `/alerts`, `/events`) from chat — and, optionally, plain questions get answered by **Oracle**, the AI gateway running on the Laptop.

Hermes does **not** run AI. Anything involving models, embeddings, speech, vision, or semantic search belongs on the Oracle laptop; Hermes only calls its REST API over Tailscale. Anton is a storage/operations server and never loads models.

---

## Contents

- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Event model](#event-model)
- [Rules](#rules)
- [Remediation (act, not just notify)](#remediation-act-not-just-notify)
- [Storm detection](#storm-detection)
- [Telegram bot (inbound)](#telegram-bot-inbound)
- [AI questions via Oracle](#ai-questions-via-oracle)
- [Providers](#providers)
- [API reference](#api-reference)
- [Project layout](#project-layout)
- [How to add a provider](#how-to-add-a-provider)
- [How to add a rule](#how-to-add-a-rule)
- [How to develop](#how-to-develop)
- [Testing and code quality](#testing-and-code-quality)
- [Roadmap](#roadmap)

---

## Architecture

```
   Watcher ─┐
   Phoenix ─┤
   Legacy ──┼──►  POST /event  ──►  Hermes  ──►  Discord
   Guardian─┤         │             │  │          Telegram
   Future ──┘         │             │  └─────────►  Email (SMTP)
                      │             │              ntfy
                 (validated)        │              Webhook
                      │             │
                      ▼             ▼
                  SQLite      Rule engine + queue
                 (events)     (log / notify / remediate)
                      ▲             │
                      │             ▼
              Storm detector   Remediation actions
              (burst collapse)  (http / command / docker_restart)
                      ▲
                      │
              Telegram bot (inbound) ──► Oracle (Lappy) ──► Ollama
              (commands + AI questions)   (Tailscale)      (local models)
```

Key properties:

- **No module knows about Discord, Telegram, Email, etc.** They only speak the Hermes event schema.
- **The API never blocks on delivery.** Events are persisted immediately and handed to an async queue; notification dispatch happens in background workers.
- **Everything is data-driven.** Rules live in YAML, secrets live in the environment, and messages are Jinja2 templates.
- **Reliable by construction.** An outbox sweep and crash recovery guarantee that stored events eventually get processed, even if Hermes is restarted mid-dispatch.
- **It can act, not just talk.** Rules can run remediation actions; every attempt is recorded in the `remediations` table.
- **Noise is handled for you.** The storm detector collapses bursts of repeated events into a single `hermes/event.storm` event.
- **Operators can talk back.** The inbound Telegram bot answers questions from the event database.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the detailed design.

---

## Quick start

### 1. Run with Docker

```bash
cd /opt/anton/hermes
cp .env.example .env          # then edit .env and fill in real provider credentials
docker compose up -d --build
docker compose ps             # wait until health shows healthy
```

Hermes listens on `http://localhost:8000`. Interactive API docs are at `http://localhost:8000/docs`.

### 2. Send your first event

```bash
curl -s -X POST http://localhost:8000/event \
  -H "Content-Type: application/json" \
  -d '{
    "module": "watcher",
    "type": "disk.usage",
    "severity": "warning",
    "title": "Disk usage high",
    "message": "/data is at 85%",
    "metadata": {"usage_percent": 85.0, "mount": "/data"},
    "tags": ["storage"]
  }'
```

```bash
curl -s http://localhost:8000/events | python3 -m json.tool
curl -s http://localhost:8000/health
```

### 3. Run without Docker (development)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
.venv/bin/uvicorn app.main:app --reload
```

---

## Configuration

All configuration is read from environment variables (or a `.env` file). Every variable is prefixed with `HERMES_`. Secrets are **never** hardcoded or committed — copy `.env.example` to `.env` and fill it in.

| Variable | Default | Purpose |
|---|---|---|
| `HERMES_ENVIRONMENT` | `development` | Environment label used in health/log output |
| `HERMES_LOG_LEVEL` | `INFO` | Root log level (structured JSON logging) |
| `HERMES_DATABASE_URL` | `sqlite+aiosqlite:///./data/hermes.db` | SQLAlchemy async URL |
| `HERMES_RULES_FILE` | `app/config/rules.yaml` | Path to the YAML rule file |
| `HERMES_TEMPLATES_DIR` | `app/templates` | Jinja2 template directory |
| `HERMES_MAX_PAGINATION_LIMIT` | `100` | Hard cap on `GET /events` `limit` |
| `HERMES_WORKER_CONCURRENCY` | `4` | Number of delivery worker tasks |
| `HERMES_WORKER_SWEEP_INTERVAL` | `30` | Seconds between outbox sweeps |
| `HERMES_NOTIFICATION_MAX_ATTEMPTS` | `3` | Delivery retries per provider |
| `HERMES_NOTIFICATION_RETRY_BASE_DELAY` | `1.0` | Exponential backoff base (seconds) |
| `HERMES_DISCORD_WEBHOOK_URL` | — | Discord webhook URL |
| `HERMES_TELEGRAM_BOT_TOKEN` | — | Telegram bot token |
| `HERMES_TELEGRAM_CHAT_ID` | — | Telegram chat/channel id |
| `HERMES_SMTP_HOST/PORT/USERNAME/PASSWORD/FROM/TO` | — | SMTP settings (`TO` is a comma-separated list) |
| `HERMES_SMTP_USE_TLS` / `HERMES_SMTP_USE_SSL` | `true` / `false` | StartTLS vs direct SSL |
| `HERMES_NTFY_URL` | `https://ntfy.sh` | ntfy server base URL |
| `HERMES_NTFY_TOPIC` | — | ntfy topic to publish to |
| `HERMES_NTFY_TOKEN` | — | ntfy access token |
| `HERMES_WEBHOOK_URL` | — | Generic webhook target |
| `HERMES_WEBHOOK_SECRET` | — | Sent as `Authorization: Bearer …` |
| `HERMES_STORM_ENABLED` | `false` | Enable burst/storm detection |
| `HERMES_STORM_WINDOW_SECONDS` | `60` | Rolling window for counting events |
| `HERMES_STORM_THRESHOLD` | `20` | Events per `(module, type)` needed to fire a storm |
| `HERMES_STORM_COOLDOWN_SECONDS` | `300` | Min time between storms of the same kind |
| `HERMES_STORM_CHECK_INTERVAL` | `10` | Seconds between detector scans |
| `HERMES_REMEDIATION_ENABLED` | `false` | Master switch for `action: remediate` rules |
| `HERMES_REMEDIATION_ALLOWED_COMMANDS` | — | Comma-separated fnmatch patterns allowed for `command` remediations |
| `HERMES_BOT_ENABLED` | `false` | Enable the inbound Telegram bot |
| `HERMES_BOT_ALLOW_CMD` | `false` | Allow the bot's `/cmd` shell command |
| `HERMES_BOT_MAX_OUTPUT_CHARS` | `3500` | Truncation limit for bot output |
| `HERMES_BOT_RATE_LIMIT_PER_MINUTE` | `10` | Max bot commands per chat per minute |
| `HERMES_NETDATA_URL` | `http://127.0.0.1:19999` | Netdata used by the bot's `/health` |
| `HERMES_AI_ENABLED` | `false` | Answer plain Telegram messages via the Oracle gateway |
| `HERMES_ORACLE_URL` | — | Oracle gateway on the laptop, e.g. `http://100.84.233.111:8003` |
| `HERMES_ORACLE_TOKEN` | — | Shared secret (same as the laptop's `ORACLE_SHARED_TOKEN`) |
| `HERMES_AI_MAX_HISTORY` | `10` | Conversation turns kept per chat (context for follow-ups) |
| `HERMES_AI_TIMEOUT` | `60` | Max seconds to wait for an Oracle answer |

A provider is **enabled** automatically when its required settings are present.

---

## Event model

Every event carries the following fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | UUID | server | Assigned on ingestion |
| `timestamp` | ISO-8601 | server | Assigned on ingestion |
| `module` | string | yes | Emitting module, e.g. `watcher`, `phoenix` |
| `type` | string | yes | Event type within the module, e.g. `disk.usage` |
| `severity` | enum | no | `debug` \| `info` \| `warning` \| `error` \| `critical` (default `info`) |
| `title` | string | yes | Short human-readable summary |
| `message` | string | no | Detailed text |
| `metadata` | object | no | Free-form structured data |
| `tags` | list[string] | no | Indexable labels |
| `correlation_id` | UUID | no | Client-supplied correlation id (generated if omitted) |

`correlation_id` lets you group related events across modules (e.g. a request that spans Watcher → Phoenix).

---

## Rules

Rules decide what happens to each event. They are evaluated **top-down**; the first matching rule wins. The default (no match / empty file) is **log**.

Rules live in `app/config/rules.yaml` (override with `HERMES_RULES_FILE`). See [How to add a rule](#how-to-add-a-rule).

```yaml
version: 1

rules:
  - name: "ignore_debug_watcher"
    action: ignore
    when:
      module: "watcher"
      severity: "debug"

  - name: "notify_errors"
    action: notify
    when:
      severity: "error"
    providers: ["all"]

  - name: "restart_gitea_on_crash"
    action: remediate
    when:
      module: "gitea"
      severity: "critical"
    remediation:
      kind: docker_restart
      container: "gitea"
    providers: ["telegram"]   # optional: notify after acting

  - name: "default_log"
    action: log
    when: {}
```

- `action`: `ignore` | `log` | `notify` | `remediate`
- `when`: field → expected value
  - strings support `fnmatch` wildcards (`disk.*`)
  - a **list** matches if the event value is one of the listed values
  - the special `tags` field matches if any event tag equals / wildcards the value
- `providers`: required when `action: notify`; optional when `action: remediate` (notifies after acting). `"all"` means every enabled provider. Unknown or disabled providers are skipped with a warning.
- `remediation`: required when `action: remediate` — see [Remediation](#remediation-act-not-just-notify).

---

## Remediation (act, not just notify)

A rule with `action: remediate` runs a remediation action in the background worker instead of (or in addition to) notifying. Every attempt is stored in the `remediations` table and the event outcome is `remediated` / `remediation_failed`.

**All remediation is disabled unless `HERMES_REMEDIATION_ENABLED=true`.** Three kinds are supported:

| Kind | `remediation` keys | Example |
|---|---|---|
| `http` | `method`, `url`, `headers`, `body`, `timeout` | Hit the Portainer API to restart a stack |
| `command` | `command`, `timeout` | Run a shell command on the Hermes host |
| `docker_restart` | `container` | `docker restart <container>` |

```yaml
  - name: "restart_immich_ml_on_crash"
    action: remediate
    when:
      module: "immich"
      type: "machine_learning"
      severity: "critical"
    remediation:
      kind: http
      method: post
      url: "http://host.docker.internal:9000/api/endpoints/1/docker/containers/immich_machine_learning/restart"
      headers:
        X-API-Key: "{{ metadata.portainer_key }}"
    providers: ["telegram"]
```

- `http` URLs, headers and JSON `body` values are **Jinja2 templates** over the event (use `{{ event.* }}` / `{{ metadata.* }}`).
- `command` remediations can be locked down with `HERMES_REMEDIATION_ALLOWED_COMMANDS`, a comma-separated list of fnmatch patterns (e.g. `systemctl restart *, docker restart *`). If set, a command that matches none of the patterns is refused.
- `docker_restart` runs `docker restart <container>` and requires the docker CLI plus access to the docker socket from the Hermes runtime (see `docker-compose.yml`).
- Remediation runs in the worker, so it never blocks the HTTP API.

---

## Storm detection

The storm detector watches the event stream and collapses bursts of repeated events. Every `HERMES_STORM_CHECK_INTERVAL` seconds it counts events grouped by `(module, type)` over a rolling `HERMES_STORM_WINDOW_SECONDS` window. A group that reached `HERMES_STORM_THRESHOLD` events is re-emitted as a single `hermes/event.storm` **event**:

- severity `warning`, tags include `storm` plus the source `module`/`type`,
- `metadata` carries `source_module`, `source_type`, `count` and `window_seconds`,
- it flows through the normal rules pipeline, so you rule on it like any event.

A per-group cooldown (`HERMES_STORM_COOLDOWN_SECONDS`) prevents re-firing while the storm is ongoing, and Hermes' own events are excluded from counting.

```yaml
  - name: "escalate_storm"
    action: notify
    when:
      module: "hermes"
      type: "event.storm"
    providers: ["telegram"]
```

This is the recommended pattern for noisy modules: individual events are logged (or ignored) by rules, and Telegram only gets pinged once per storm.

---

## Telegram bot (inbound)

When `HERMES_BOT_ENABLED=true` (plus Telegram credentials), Hermes long-polls Telegram and answers commands from the chats listed in `HERMES_TELEGRAM_CHAT_ID`:

```
/status    Hermes health, queue size and recent activity
/alerts    last 5 error/critical events (from the Hermes database)
/events    last 5 events
/providers which notification providers are enabled
/health    host summary fetched from Netdata (HERMES_NETDATA_URL)
/cmd       run a shell command on the host (HERMES_BOT_ALLOW_CMD=true)
/help      this help
```

- Commands from chats **not** in `HERMES_TELEGRAM_CHAT_ID` are ignored.
- Commands are rate-limited per chat (`HERMES_BOT_RATE_LIMIT_PER_MINUTE`).
- `/cmd` is **off** by default and gated by `HERMES_BOT_ALLOW_CMD`.
- Every command is recorded as a `hermes/bot.command` event, so bot traffic is subject to the same rules and storm detection as everything else.
- From inside the Hermes container, point `HERMES_NETDATA_URL` at the host (e.g. `http://host.docker.internal:19999`; `docker-compose.yml` already maps `host-gateway`).
- The old standalone `scripts/anton_bot.py` was retired: Telegram only allows one long-polling instance per bot token, and the in-app bot supersedes it. It remains in the repo for reference.

---

## AI questions via Oracle

When `HERMES_AI_ENABLED=true`, any **non-command** Telegram message from an allowed chat is forwarded to the **Oracle** AI gateway and the model's reply is sent back. Commands (`/status`, …) still work as before.

```
User ──► Telegram ──► Hermes bot (Anton) ──► Oracle (laptop, tailnet) ──► Ollama
        ◄──────────────────── reply ───────────────────────────────────────┘
```

- Oracle runs on the **Laptop** (`oracle/` in this repo — a self-contained FastAPI service wrapping Ollama). Anton never loads models; Hermes is only an HTTP client.
- Point `HERMES_ORACLE_URL` at the gateway's Tailscale address, e.g. `http://100.84.233.111:8003`, and set `HERMES_ORACLE_TOKEN` to the same value as the laptop's `ORACLE_SHARED_TOKEN`.
- Conversation history is kept per chat (bounded by `HERMES_AI_MAX_HISTORY`), so follow-up questions have context. The gateway itself is stateless.
- Every exchange is recorded as a `hermes/bot.ask` event (`severity: warning` if the gateway was unreachable).
- If the laptop is offline the bot replies `⚠️ Oracle unavailable …` instead of hanging.
- See [`oracle/README.md`](oracle/README.md) for the laptop setup, auth and the Tailscale binding.

---

## Providers

Hermes ships with five providers. Each implements the same interface.

| Provider | Name (rule) | Enabled when |
|---|---|---|
| Discord | `discord` | `HERMES_DISCORD_WEBHOOK_URL` set |
| Telegram | `telegram` | `HERMES_TELEGRAM_BOT_TOKEN` **and** `HERMES_TELEGRAM_CHAT_ID` set |
| Email (SMTP) | `email` | `HERMES_SMTP_HOST`, `HERMES_SMTP_FROM`, `HERMES_SMTP_TO` set |
| ntfy | `ntfy` | `HERMES_NTFY_TOPIC` set |
| Webhook | `webhook` | `HERMES_WEBHOOK_URL` set |

Delivery is **retried** up to `HERMES_NOTIFICATION_MAX_ATTEMPTS` times with exponential backoff. Each attempt is recorded in the `notifications` table.

---

## API reference

Interactive docs: `http://localhost:8000/docs`.

### `POST /event`

Ingest an event. Returns **202 Accepted** immediately with the event id. Processing is asynchronous.

Request body: see [Event model](#event-model).

Response:

```json
{ "id": "3f2504e0-…", "status": "queued" }
```

### `GET /events`

List stored events, newest first. Query parameters:

- `module`, `type`, `severity` — optional filters
- `limit` — page size (default `50`, capped at `HERMES_MAX_PAGINATION_LIMIT`)
- `offset` — page offset

Response:

```json
{
  "items": [ { "id": "…", "timestamp": "…", "module": "watcher", … } ],
  "pagination": { "limit": 50, "offset": 0, "total": 123, "next_offset": 50 }
}
```

### `GET /health`

```json
{
  "status": "ok",
  "version": "1.0.0",
  "environment": "development",
  "database": "ok",
  "queue": { "size": 0 },
  "bot": { "enabled": false },
  "storm": { "enabled": true }
}
```

Every response includes an `X-Request-ID` header; the same id is attached to all logs emitted while serving that request.

---

## Project layout

```
hermes/
├── app/
│   ├── main.py               # Application factory + lifespan wiring
│   ├── api/
│   │   ├── middleware.py     # Request-id ASGI middleware
│   │   └── routes/           # health.py, events.py
│   ├── bot/
│   │   └── telegram_bot.py   # Inbound Telegram bot (long-polling commands)
│   ├── config/
│   │   ├── settings.py       # pydantic-settings, env-based
│   │   └── rules.yaml        # default rule set
│   ├── core/
│   │   ├── logging.py        # structured JSON logging
│   │   ├── renderer.py       # Jinja2 template rendering
│   │   └── queue.py          # async delivery queue + outbox sweep
│   ├── database/
│   │   ├── base.py           # SQLAlchemy declarative base
│   │   ├── models.py         # events, notifications + remediations tables
│   │   └── session.py        # async engine / session factory
│   ├── models/
│   │   └── event.py          # Pydantic event schema
│   ├── providers/
│   │   ├── base.py           # provider interface
│   │   ├── registry.py       # provider registry + "all" resolution
│   │   ├── discord.py / telegram.py / email.py / ntfy.py / webhook.py
│   ├── rules/
│   │   ├── models.py         # YAML schema (pydantic) incl. remediation
│   │   ├── loader.py         # YAML -> validated RulesFile
│   │   └── engine.py         # first-match-wins evaluation
│   ├── services/
│   │   ├── event_service.py  # ingest + query (API side)
│   │   ├── dispatcher.py     # rules -> render -> deliver / remediate (worker side)
│   │   ├── remediator.py     # http / command / docker_restart actions
│   │   ├── storm.py          # burst detection -> hermes/event.storm
│   │   └── oracle.py         # HTTP client for the Oracle AI gateway
│   └── templates/            # per-provider Jinja2 templates
├── oracle/                   # Oracle AI gateway (deployed on Lappy, not Anton)
│   ├── app/                  # FastAPI service wrapping Ollama
│   └── README.md             # Lappy / Windows setup
├── tests/                    # pytest suite
├── docs/
│   └── ARCHITECTURE.md
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── requirements-dev.txt
├── .env.example
└── pyproject.toml
```

---

## How to add a provider

Adding a provider is exactly **one new class** plus one line in the registry.

1. **Create `app/providers/awesome.py`**:

   ```python
   from app.config.settings import Settings
   from app.providers.base import BaseProvider, ProviderError, ProviderMessage

   class AwesomeProvider(BaseProvider):
       name = "awesome"
       templates = {"text": "awesome.j2"}   # slot -> template file

       @property
       def enabled(self) -> bool:
           return bool(self.settings.awesome_url)

       async def send(self, message: ProviderMessage) -> None:
           # renderered[slot] holds the template output
           response = await self._client.post(self.settings.awesome_url, json={
               "text": message.rendered["text"],
               "event_id": message.event_id,
           })
           response.raise_for_status()  # convert to ProviderError on failure
   ```

2. **Add a template** `app/templates/awesome.j2` using `event`, `severity`, `emoji`.

3. **Register it** in `app/providers/registry.py` (import it and add it to the tuple in `ProviderRegistry.__init__`).

4. **Add settings** in `app/config/settings.py` and `.env.example` (e.g. `HERMES_AWESOME_URL`).

5. (Recommended) add a test in `tests/test_providers.py` using `httpx.MockTransport`.

That's it. Rules can now target `providers: ["awesome"]`.

---

## How to add a rule

Edit `app/config/rules.yaml` and restart Hermes (the file is read once at startup).

```yaml
  - name: "page_ops_on_immich_failures"
    action: notify
    when:
      module: "immich"
      severity: "error"
    providers: ["telegram", "ntfy"]
```

Rules are **declarative** — no Python changes needed. The engine (first match wins) and the full matching syntax are documented in [Rules](#rules).

---

## How to develop

```bash
# setup
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env

# run with auto-reload
.venv/bin/uvicorn app.main:app --reload

# test
.venv/bin/python -m pytest

# quality gates
.venv/bin/black --check app tests
.venv/bin/ruff check app tests
.venv/bin/mypy app
```

Conventions:

- Python 3.12, `from __future__ import annotations`, type hints everywhere.
- No AI, no embeddings, no vector DBs — those live on the Oracle laptop.
- Secrets only via environment; never in code.
- Keep modules small and single-purpose; tests accompany every behavior.

## Testing and code quality

- **Tests:** `pytest` (39 tests) covering the API, rule engine, providers, dispatcher, and queue recovery.
- **Formatting:** Black (`--check`).
- **Linting:** Ruff.
- **Types:** mypy (production code).

These run automatically in CI; run them locally before committing.

---

## Roadmap

Done in this revision:

- **Inbound Telegram bot** — long-polling commands (`/status`, `/alerts`, `/events`, `/providers`, `/health`, `/cmd`) answered from the Hermes database, with chat allow-list, rate limiting and command audit events.
- **Remediation** — `action: remediate` rules that run `http`, `command` or `docker_restart` actions from the worker, recorded in a `remediations` table. Off unless `HERMES_REMEDIATION_ENABLED=true`.
- **Storm detection** — collapses bursts of repeated events into a single `hermes/event.storm` event with per-group cooldown.
- **AI questions via Oracle** — plain Telegram messages are answered by the Oracle gateway on Lappy (`oracle/`); Hermes only acts as an HTTP client, per the no-AI-on-Anton invariant.

Still open (plumbing):

- **Hermes v2**:
  - PostgreSQL migration (swap `HERMES_DATABASE_URL`, no code changes needed).
  - Redis / message-broker transport behind the `NotificationQueue` interface.
  - Rule hot-reload (watch the YAML file instead of restarting).
  - Provider rate limiting and per-provider throttling.
  - Alert deduplication / grouping by `correlation_id`.
- **Scheduled digests** — daily/weekly summaries driven by rules with a `schedule` key.
- **More Oracle capabilities** — summarization / classification as *optional* rule actions, still via the Lappy REST API.
