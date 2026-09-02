# Watcher

LAN device intelligence and presence monitoring for the Anton home-lab.

## Components

| Path | Purpose |
|------|---------|
| `app.py` | FastAPI read layer over the WatchYourLAN Postgres DB (devices, events, aliases, summary) |
| `watcher_memory.py` | Long-running agent that ingests WatchYourLAN state, writes to Postgres, and publishes device discovered/online/offline events to Hermes |
| `presence.py` | Minimal presence loop that publishes device state transitions to Hermes |
| `poller.py` | Simple polling loop that prints device states to stdout |
| `docker-compose.yml` | Build/run config for the API service |
| `infra-compose.yml` | Infrastructure services (Postgres, WatchYourLAN, ntopng, Redis) |

## Requirements

- Python 3.12+
- `pip install -r requirements.txt`
- A PostgreSQL database and a running WatchYourLAN instance

## Configuration

Driven by environment variables (see each file for names and defaults):

- `WATCHER_DB_*` — Postgres connection (host, port, name, user, password)
- `WATCHER_ALIASES_FILE` — path to `aliases.json`

`aliases.json` maps raw MAC addresses to human-readable host names.

## Examples

```bash
# Run the API in-process
uvicorn app:app --port 8000

# Run the memory agent
python watcher_memory.py
```

## License

Private / internal. Do not redistribute without permission.
