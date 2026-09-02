# LEGACY

> "The wars are over. Yet my heart still draws its sword."

A production-grade memory service for the Anton ecosystem. Provides persistent memory, semantic search, knowledge graphs, and agent integration.

## Features

- **Memory Management** — Journal entries, reflections, observations with rich metadata
- **Semantic Search** — Vector embeddings via sentence-transformers
- **Knowledge Graph** — Automatic entity extraction and relationship mapping
- **Authentication** — Local accounts, session cookies, API keys, role-based access
- **Permissions** — Private, shared, public, agent-only, system visibility levels
- **Background Queue** — Priority-based job queue with retries
- **Export/Import** — JSON, CSV, Markdown formats
- **Backup System** — One-click and scheduled encrypted backups
- **Audit Logging** — Full audit trail of all operations
- **Health Checks** — /health, /ready, /live endpoints
- **Monitoring** — Metrics, structured logging, request tracing
- **OpenAPI** — Complete API documentation at /api/docs

## Quick Start

```bash
docker-compose up -d
# Access at http://localhost:5050
# Default: admin / admin
```

## Documentation

- [Architecture](docs/Architecture.md)
- [API Reference](docs/API.md)
- [Deployment Guide](docs/Deployment.md)
- [Memory System](docs/Memory.md)
- [Developer Guide](docs/Developer.md)

## Configuration

Edit `config.yaml` to customize:

```yaml
app:
  secret_key: "your-secret-key"    # Change in production
  host: "0.0.0.0"
  port: 8000

auth:
  enabled: true                    # Enable authentication

database:
  url: "sqlite:///database/legacy.db"  # Or PostgreSQL
```

## Tech Stack

- **Backend:** Python 3.11, FastAPI, SQLAlchemy
- **Database:** SQLite (default) / PostgreSQL
- **Search:** sentence-transformers (all-MiniLM-L6-v2)
- **Frontend:** Jinja2 templates, vanilla JS, Chart.js, vis-network
- **Infrastructure:** Docker, Docker Compose

## License

MIT
