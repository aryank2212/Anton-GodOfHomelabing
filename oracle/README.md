# Oracle — AI gateway for Anton

**Component:** Oracle Core v1
**Where it runs:** the Laptop (`ak` — Linux, `100.84.233.111`), native Python + local Ollama
**Role:** answers questions for Anton's Hermes bot using **local** models — the
Anton server never loads or executes AI models.

```
User ──► Telegram ──► Hermes (Anton) ──► Oracle (laptop, via tailnet) ──► Ollama
        ◄──────────────── reply ───────────────────────────────────────────┘
```

## Endpoints

| Route | Auth | Purpose |
|---|---|---|
| `POST /v1/ask` | Bearer token | `{message, history[]}` → `{reply, model, tokens, latency_ms}` |
| `GET /health` | none | `{status, model, ollama: up/down}` |

`history` is a list of `{role: user|assistant, content}` turns (bounded by
`ORACLE_MAX_HISTORY`); the gateway prepends the system prompt and appends the
new message.

## Setup (Laptop — Linux)

The laptop is the `ak` Tailscale node (`100.84.233.111`). Deployed at
`~/oracle` and run as a `systemd` **user** service:

1. Copy the project to the laptop (e.g. `~/oracle`), then:
   ```bash
   cd oracle
   python3 -m venv .venv            # needs python3-venv on Debian/Ubuntu
   .venv/bin/pip install -r requirements.txt
   cp .env.example .env
   ```
2. Edit `.env`:
   - `ORACLE_MODEL` — pick a model from `ollama list` (e.g. `qwen3:8b`, `llama3`).
   - `ORACLE_SHARED_TOKEN` — a long random string. **Use the same value on Anton**
     as `HERMES_ORACLE_TOKEN`.
   - `ORACLE_HOST=100.84.233.111` — the laptop's Tailscale IP, so only the tailnet
     can reach the gateway.
3. Start it (as a persistent user service, so it survives reboots/logout):
   ```bash
   mkdir -p ~/.config/systemd/user
   # ~/.config/systemd/user/oracle.service, see unit in the repo notes:
   #   ExecStart=%h/oracle/.venv/bin/python -m uvicorn app.main:app \
   #     --host 100.84.233.111 --port 8003
   loginctl enable-linger "$USER"     # so the service runs without a login
   systemctl --user daemon-reload
   systemctl --user enable --now oracle.service
   ```
   (or `./run.sh` after setting `.env`).
4. Verify from Anton (or any Tailnet machine):
   ```bash
   curl -s http://100.84.233.111:8003/health
   curl -s -X POST http://100.84.233.111:8003/v1/ask \
     -H "Authorization: Bearer <token>" \
     -H "Content-Type: application/json" \
     -d '{"message": "hello", "history": []}'
   ```

## Security

- The gateway only ever listens on the Tailscale interface (`ORACLE_HOST` set to
  the tailnet IP); it never binds a public interface.
- `/v1/ask` requires the shared Bearer token; compare it with a Tailscale ACL
  allowing only `anton → ak:8003` for defense in depth.
- `ORACLE_SHARED_TOKEN` is environment-only; `.env` is git-ignored.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest
black --check app tests
ruff check app tests
```
