# Hermes — Architecture

**Component:** Hermes Core v1
**Project:** ANTON (modular self-hosted personal operating system)
**Role:** the single communication and event system for Anton

---

## 1. Place in the system

Anton is split across two machines:

| Machine | Responsibilities |
|---|---|
| **ANTON server** | Containers, APIs, event collection, DBs, storage, journaling, Watcher, Phoenix, Legacy, Guardian, Gitea, Jellyfin, Paperless, Immich, scheduling, rule engine, **notifications (Hermes)**, logging, backups. Lightweight: **no** LLM inference, embeddings, CUDA, Ollama, Whisper, or AI vector DBs. |
| **LAPTOP (Oracle)** | Ollama, local LLMs, embeddings, Whisper, OCR AI, face/object recognition, semantic search, AI summarization/reasoning/planning, vision, future CUDA. |

Hermes runs **only** on the Anton server. It never loads or executes AI models; any future AI consumption happens through REST APIs provided by Oracle.

**Invariant:** no Anton module (other than Hermes) may know about Discord, Telegram, Email, ntfy, or any notification provider.

```
Watcher ─┐
Phoenix ─┤
Legacy ──┼─► POST /event ─► Hermes ─► providers
Guardian─┤                    │
Future ──┘                    ├─► remediations (act: http / command / docker_restart)
                              ├─► SQLite (events + delivery state)
                              ├─► Storm detector → hermes/event.storm
                              └─► Telegram bot (inbound) ─► Oracle (Lappy) ─► Ollama
```

---

## 2. Design goals

1. **Single choke point for notifications.** All eventing flows through Hermes.
2. **Non-blocking API.** `POST /event` persists and returns immediately; delivery is asynchronous.
3. **Zero code changes to route events.** Rules are data (YAML); providers are pluggable classes.
4. **Durable delivery.** The database is the source of truth; the in-process queue is an optimization, not a dependency.
5. **Future-proof storage.** SQLAlchemy 2.0 with an async engine; SQLite today, PostgreSQL by changing one URL.
6. **Minimal dependencies, maintainable code.** Small single-purpose modules, Python 3.12 idioms.

---

## 3. Request lifecycle

### 3.1 Ingestion (`POST /event`)

1. FastAPI validates the JSON body against `EventCreate` (extra fields are rejected).
2. `EventService.create` builds an `EventRecord` and persists it (`state = pending`).
3. The event id is handed to the `NotificationQueue` via `put()` — an in-memory `asyncio.Queue` operation that never blocks the request.
4. `202 Accepted` returns immediately with `{id, status: "queued"}`.

The API layer never calls a provider, renders a template, or evaluates a rule.

### 3.2 Delivery (background workers)

1. A worker pulls an event id and calls `Dispatcher.process`.
2. `Dispatcher._prepare` **claims** the event with a conditional UPDATE
   (`pending -> processing`). If the row count is zero the event was already
   claimed by another worker and the call is a no-op (idempotency under races).
3. `RuleEngine.evaluate` runs the event through the YAML rules (first match wins).
4. Depending on the decision:
   - **ignore** → `state = done`, `outcome = ignored`.
   - **log** → `state = done`, `outcome = logged`.
   - **notify** → one `NotificationRecord` (`status = pending`) is created per
     resolved provider, then each provider is invoked concurrently.
   - **remediate** → a `RemediationRecord` is created and the action runs in
     the worker. When the rule also lists `providers`, notifications are sent
     afterwards. Outcome = `remediated` | `remediation_failed`.
5. Each delivery is retried up to `HERMES_NOTIFICATION_MAX_ATTEMPTS` times with
   exponential backoff. Success/failure, attempt count, and the last error are
   written back to the `NotificationRecord`.
6. The event is finalized (`state = done`) with an aggregate outcome:
   `notified` | `partial` | `failed` | `skipped` | `remediated` | `remediation_failed`.

### 3.3 Reliability (outbox)

- A periodic **sweep** re-enqueues every event still in `pending`, and on
  startup any event stuck in `processing` (a crashed process) is reset to
  `pending`. This guarantees at-least-once processing: events are never lost
  because Hermes restarted.
- Duplicate delivery is prevented by the claim UPDATE (only one worker can
  claim a `pending` event).

---

## 4. Components

### 4.1 API layer (`app/api`)

| Route | Purpose |
|---|---|
| `POST /event` | Ingest a validated event → 202 with id |
| `GET /events` | Paginated listing with optional `module`/`type`/`severity` filters |
| `GET /health` | Liveness: DB reachability + queue size |

`RequestIDMiddleware` (pure ASGI) assigns an `X-Request-ID` to every request,
stores it in a `ContextVar`, and echoes it on the response so request logs are
correlatable.

### 4.2 Event model (`app/models`, `app/database`)

Pydantic models describe the wire schema; SQLAlchemy models describe storage.

- `events` — id (UUID), timestamp, module, type, severity, title, message,
  metadata (JSON), tags (JSON), correlation_id, state, outcome, timestamps.
- `notifications` — per-provider delivery attempts: provider, status
  (`pending|sent|failed`), attempts, error.
- `remediations` — per-event remediation attempts: rule, kind, target, status
  (`pending|done|failed`), attempts, detail.

`metadata`/`tags` use the portable `JSON` column type so the schema migrates to
PostgreSQL unchanged.

### 4.3 Rule engine (`app/rules`)

- `loader.py` reads YAML → validated `RulesFile` (pydantic). A missing file
  yields an empty rule set (fallback = log); an invalid file fails fast.
- `engine.py` evaluates rules **first-match-wins**. Matching supports equality,
  `fnmatch` wildcards on strings, list membership, and tag matching. A notify
  rule without providers (or a remediate rule without `remediation`) is
  rejected at load time.

### 4.4 Provider system (`app/providers`)

- `base.py` defines `BaseProvider` (name, templates, `enabled`, async `send`).
- `registry.py` owns instances and resolves rule targets. `"all"` expands to
  every enabled provider; unknown/disabled providers are skipped with a warning.
- Implementations: `discord`, `telegram`, `email` (SMTP in an executor),
  `ntfy`, `webhook`. Each keeps its own `httpx.AsyncClient`.

### 4.5 Rendering (`app/core/renderer.py`, `app/templates`)

Providers declare `templates = {slot: file}`. The renderer builds one context
per event (`event`, `severity`, `emoji`) and renders each slot with Jinja2.
Email renders a subject + HTML body; the webhook provider renders a JSON
document consumed by `json.loads`.

### 4.6 Queue (`app/core/queue.py`)

`NotificationQueue` is deliberately a thin facade (`start`, `stop`, `put`,
`size`) over an `asyncio.Queue` + worker pool + outbox sweep. Swapping the
transport to Redis or a message broker later only replaces this class.

### 4.7 Remediation (`app/services/remediator.py`)

Rules with `action: remediate` are executed by the `Remediator` in the worker.
Kinds: `http` (templated URL/headers/body), `command` (subprocess on the host),
`docker_restart`. All remediation is off unless `HERMES_REMEDIATION_ENABLED`
is true; `command` actions can be restricted to `HERMES_REMEDIATION_ALLOWED_COMMANDS`
fnmatch patterns. Results land in the `remediations` table.

### 4.8 Storm detection (`app/services/storm.py`)

The `StormDetector` background task counts events grouped by `(module, type)`
over a rolling window and re-emits any group over `HERMES_STORM_THRESHOLD` as a
single `hermes/event.storm` event (with per-group cooldown). Storm events flow
through the normal rules pipeline, so operators rule on storms instead of the
individual noisy events.

### 4.9 Telegram bot (`app/bot/telegram_bot.py`)

The inbound bot long-polls Telegram (`getUpdates`) when `HERMES_BOT_ENABLED`.
Only chats in `HERMES_TELEGRAM_CHAT_ID` may issue commands. Answers are built
from the Hermes database (`/status`, `/alerts`, `/events`, `/providers`) and
Netdata (`/health`); `/cmd` is gated by `HERMES_BOT_ALLOW_CMD`. Every command
is recorded as a `hermes/bot.command` event.

### 4.10 AI questions (`app/services/oracle.py`)

When `HERMES_AI_ENABLED`, non-command messages from allowed chats are sent to
the **Oracle** gateway over Tailscale and the reply is posted back. `OracleClient`
keeps a bounded per-chat history (in-memory) and records every exchange as a
`hermes/bot.ask` event. It is a pure HTTP client — no models run on Anton; the
gateway itself lives in `oracle/` and is deployed on the Laptop.

### 4.11 Logging (`app/core/logging.py`)

Structured JSON logs, one object per line: `ts`, `level`, `logger`, `message`,
plus structured extras (`request_id`, `event_id`, `event_module`, `severity`).

### 4.12 Configuration (`app/config`)

`pydantic-settings` reads `HERMES_*` environment variables (or `.env`). Secrets
are environment-only. Provider enablement is derived from presence of the
required settings.

---

## 5. Data flow (notify example)

```
POST /event {module: watcher, type: disk.usage, severity: error}
        │
        ▼
validate → INSERT events (pending) → queue.put(id) → 202
        │
        ▼ worker
claim (pending→processing)
        ▼
RuleEngine → notify → providers: ["all"]
        ▼
INSERT notifications (pending) per enabled provider
        ▼
render template per provider ──► provider.send()  (retry ×N, backoff)
        │                              │
        ▼                              ▼
UPDATE notifications (sent|failed)    Discord / Telegram / …
        ▼
UPDATE events (done, outcome=notified|partial|failed)
```

---

## 6. Security

- Secrets only from the environment; `.env` is git-ignored; `.env.example`
  contains placeholders only.
- Docker image runs as an unprivileged user (`hermes`, uid 1000).
- Webhook/ntfy auth headers are attached per-provider when tokens are set.
- Request validation is strict (`extra="forbid"`); unknown fields are rejected.
- Remediation is opt-in (`HERMES_REMEDIATION_ENABLED`); `command` actions are
  restricted to `HERMES_REMEDIATION_ALLOWED_COMMANDS` fnmatch patterns.
- The bot only answers chats listed in `HERMES_TELEGRAM_CHAT_ID`; `/cmd` is
  disabled unless `HERMES_BOT_ALLOW_CMD` is set.
- AI calls to Oracle are authenticated with a shared Bearer token
  (`HERMES_ORACLE_TOKEN` ↔ `ORACLE_SHARED_TOKEN`) and the gateway only listens
  on the Laptop's Tailscale interface.

## 7. Operational notes

- Persistence: Docker volume `hermes_data` mounted at `/data`.
- Restart policy: `unless-stopped`; healthcheck via `GET /health`.
- Logs: `docker compose logs -f hermes` (JSON lines).

## 8. Extension points

| Concern | Where to change |
|---|---|
| New provider | one class + template + registry line |
| New routing behavior | YAML rule file |
| New message format | Jinja2 template |
| New remediation action | `Remediator.run_action` + `Remediation` model + YAML |
| New bot command | `TelegramBot.commands` + `execute` dispatch |
| New AI question type | `OracleClient` + Oracle gateway endpoint |
| New storage backend | `HERMES_DATABASE_URL` (+ optional driver) |
| New queue transport | implement `NotificationQueue` interface |
| AI features (future) | REST calls to Oracle services, never in-process |
