# Anton Argus — internet ingestion and intelligence subsystem.

Argus is Anton's internet-facing perception layer. Where Sentinel watches the
LAN, Argus watches the internet: it collects raw signals (feeds, tracked
websites, OSINT APIs, public Telegram channels), resolves the entities they
mention, correlates them across sources, detects change, proposes hypotheses
(optionally with Oracle's help) and distils it all into intelligence reports
for the Anton memory stack (PostgreSQL, Redis, evidence, knowledge graph,
archives, investigations, reports).

```
INTERNET
    │
    ▼
 ARGUS Ingestion ──collectors──> evidence (PostgreSQL/SQLite)
    │
    ▼
 Intelligence Layer: extraction → entity resolution → correlation
                    → change detection → hypothesis engine (Oracle)
    │
    ├─────► ORACLE (AI / compute)
    └─────► ANTON memory (PostgreSQL, Redis, Evidence, Knowledge Graph,
             Archives, Investigations, Reports)
    │
    ▼
 INTELLIGENCE REPORT
```

## Principles (mirrors Sentinel)

* Collectors only *collect*. They never decide, act or notify.
* Every collected item is an immutable `ContentItem` — the evidence record.
* The intelligence layer reads evidence and writes entities, relations,
  changes, hypotheses, investigations and reports.
* Argus never notifies anyone: it publishes standardized events to Hermes
  (`POST /event`) and Hermes owns notifications.

## Layout

| Path | Purpose |
|---|---|
| `app/sources` | internet collectors (rss, scrape, osint, telegram) |
| `app/intelligence` | extraction, entity resolution, correlation, change detection, hypotheses |
| `app/reports` | intelligence report generation |
| `app/database` | SQLAlchemy storage (evidence / entities / graph / changes / hypotheses / investigations / reports) |
| `app/config` | settings + `sources.yaml` loader |
| `app/api` | FastAPI (health, evidence, entities, graph, sources, changes, hypotheses, investigations, reports) |
| `config/sources.yaml` | what Argus watches (feeds, sites, providers, channels) |

## Run

```bash
cp .env.example .env
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --port 8012
```

Docker:

```bash
cp .env.example .env
docker compose up -d --build
```

The container uses `network_mode: host` so collectors can reach the internet,
Oracle over the tailnet and Hermes at `127.0.0.1:8002`.

## Web UI (Argus command center)

The repo ships a build-free static SPA in `web/` that FastAPI serves at `/`.
It exposes both monitoring views (sources, evidence, entities, changes,
hypotheses, graph, reports) and a *Command* panel to start/cancel dot
investigations, create/cancel research sessions and manage dot watches.

* Command (mutating) endpoints require a Bearer token set via
  `ARGUS_COMMAND_TOKEN`. If unset, command routes stay open (not recommended
  when exposed on the internet).
* Set the token in the browser via the **🔑 Set token** button in the sidebar;
  it is stored in `localStorage` and attached to command requests.
* Monitoring/read-only GETs under `/v1/*` stay open.

Token auth lives in `app/api/auth.py` (`require_command_token`) and is applied
in `app/api/routes/dots.py` and `app/api/routes/research.py`. The SPA is plain
vanilla JS (`web/index.html`, `web/static/app.js`, `web/static/styles.css`) —
no build step — and is served by `app/main.py` when `web/index.html` exists.

## Tests

```bash
.venv/bin/python -m pytest
```

## Oracle

When `ARGUS_ORACLE_ENABLED=true`, extraction and hypothesis generation can be
delegated to the Oracle gateway (`POST /v1/ask`, Bearer token
`ARGUS_ORACLE_TOKEN`). Turn them on individually with
`ARGUS_ORACLE_EXTRACTION_ENABLED` / `ARGUS_ORACLE_HYPOTHESIS_ENABLED`.
