# LEGACY Deployment Guide

## Prerequisites

- Python 3.11+
- Docker (optional)
- At least 2GB RAM (4GB recommended for embeddings)
- 1GB disk space

## Quick Start (Docker)

```bash
# Clone and build
git clone <repo> && cd legacy
docker-compose up -d

# Default credentials: admin / admin
# Access at: http://localhost:5050
```

## Quick Start (Native)

```bash
# Python virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r backend/requirements.txt

# Configure
cp config.yaml config.yaml
# Edit config.yaml with your settings

# Run database migration
python -c "from backend.database import engine; from backend import models; models.Base.metadata.create_all(bind=engine)"

# Start
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

## Configuration

Configuration is managed through `config.yaml`:

```yaml
app:
  secret_key: "your-256-bit-secret"  # CHANGE THIS
  host: "0.0.0.0"
  port: 8000

database:
  url: "sqlite:///database/legacy.db"  # or postgresql://user:pass@host/db

auth:
  enabled: true
  session_ttl: 86400  # 24 hours

logging:
  level: INFO
  format: json
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///database/legacy.db` | Database connection string |
| `LEGACY_CONFIG` | `config.yaml` | Path to config file |
| `LOG_LEVEL` | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR) |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama server URL |
| `LLM_MODEL` | `llama3.2` | Default LLM model |

## Production Deployment

### PostgreSQL

```yaml
# config.yaml
database:
  url: "postgresql://user:password@host:5432/legacy"
  pool_size: 10
  max_overflow: 20
```

### Reverse Proxy (Nginx)

```nginx
server {
    listen 443 ssl;
    server_name legacy.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Systemd Service

```ini
[Unit]
Description=LEGACY Memory Service
After=network.target

[Service]
Type=simple
User=legacy
WorkingDirectory=/opt/legacy
ExecStart=/opt/legacy/venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## Security Checklist

- [ ] Change `secret_key` in config.yaml
- [ ] Change default admin password
- [ ] Enable HTTPS via reverse proxy
- [ ] Set appropriate CORS origins
- [ ] Enable authentication (`auth.enabled: true`)
- [ ] Configure daily backup schedule
- [ ] Set up log rotation
- [ ] Use PostgreSQL for production

## Monitoring

- `/health` - Basic health check
- `/ready` - Readiness probe
- `/live` - Liveness probe
- `/status` - Full status with queue info

## Backup

```bash
# One-click backup (admin only)
curl -X POST /api/admin/backup?backup_type=full

# List backups
curl /api/admin/backups

# Restore
curl -X POST "/api/admin/backups/restore?filename=legacy_backup_20240101_120000_full.legacy"
```

## Scaling

- Horizontal scaling with PostgreSQL backend
- Embedding model runs locally (sentence-transformers)
- Worker threads configurable via `queue.max_workers`
- Consider separate embedding server for high load
