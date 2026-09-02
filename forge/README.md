# Anton Forge

Forge is the **execution layer** for Anton: a tool API with *staged autonomy*
over Docker, Phoenix recovery and git, gated by policy and (at Level 1) human
approval via Hermes/Telegram.

It answers the Oracle agent's "hands, not just eyes" gap — the agent can now
*run* low-risk actions instead of only reporting them.

## Autonomy levels

| Level | Read tools | Act tools |
|-------|-----------|-----------|
| 0 — diagnose | allowed | refused |
| 1 — approve | allowed | require Telegram approval (default) |
| 2 — auto | allowed | preapproved low-risk actions auto-run; the rest are approved |

Cooldowns (default 15 min per target) and a crash-loop guard (3 restart-family
actions in an hour escalates to approval) back every act tool.

## Tools (22)

**Docker** `docker_ps` `docker_inspect` `docker_logs` `docker_stats`
`docker_restart` `docker_start` `docker_stop` `docker_mark_good`
`docker_rollback` `docker_scale`

**Phoenix** `phoenix_health` `phoenix_incidents` `phoenix_recover`

**Git** `git_status` `git_log` `git_mark_good` `git_rollback`

**System** `watcher_summary` `hermes_events`

**Research** `web_search` `fetch_url` `write_note`

- `web_search` — free DuckDuckGo search (no API key); returns title + URL +
  snippet per result.
- `fetch_url` — fetch an http(s) page and return readable text (tags stripped,
  ~50 KB cap).
- `write_note` — append a timestamped note to a `.txt` file in the managed
  notes folder (`FORGE_NOTES_DIR`, bind-mounted at `/data/notes`); an act tool,
  so it goes through the normal Level-1 approval flow. Notes land on the host
  at `/opt/anton/notes` and are readable by the operator.

Read tools advertise `read_only: true` and never require approval.

## API

Bearer-token protected (`FORGE_TOKEN`); caller sets `x-forge-caller` for audit.
On the host, Forge listens only on `anton-net` (service name `forge:8000`). For
the Oracle gateway on the laptop it is additionally published on the Docker
host's Tailscale IP, so it is reachable only from the tailnet:
`http://100.77.54.107:8092`.

```
GET  /health                        liveness + policy summary
GET  /v1/tools                      tool specs (advertised to the Oracle agent)
POST /v1/run                        {"tool": "...", "args": {...}, "reason": "..."}
GET  /v1/approvals                  pending approvals
POST /v1/approvals/{id}/resolve     {"approved": true, "by": "telegram"}
GET  /v1/runs?limit=50              recent audit trail
```

## Approvals

At Level 1, an act tool POST creates a pending approval and pushes it to Hermes
(`POST /v1/approvals`), which forwards it to Telegram. The operator replies
*yes* / *no*; Hermes calls back `POST /v1/approvals/{id}/resolve`. Approvals
expire after `FORGE_APPROVAL_TIMEOUT` (default 600 s).

## Config

- `forge.yaml` (mounted at `/app/forge.yaml`): autonomy level, managed repos,
  compose projects, preapprovals, cooldowns. Example in `forge.yaml.example`.
- `.env` (`FORGE_*`): token, upstream URLs, state/audit paths. Example in
  `.env.example`.

State (known-good image/git refs) lives in `/data/forge-state.json`; every
decision is appended to `/data/forge-audit.jsonl`.

## Layout

```
app/config.py       settings (FORGE_*)
app/policy.py       staged-autonomy policy
app/state.py        known-good image / git ref store
app/engine.py       decision -> approval -> execution, cooldown, crash-loop guard
app/approval.py     pending approval store
app/audit.py        JSONL audit log
app/clients/docker.py  Docker SDK wrapper (rollback preserves config/hostconfig)
app/tools/*         tool implementations
app/tools/registry.py  wiring of all 22 tools
app/main.py         FastAPI app
```
