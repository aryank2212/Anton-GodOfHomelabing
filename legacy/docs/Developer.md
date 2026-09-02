# LEGACY Developer Guide

## Development Setup

```bash
# Clone
git clone <repo> && cd legacy

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dev dependencies
pip install -r backend/requirements.txt
pip install pytest httpx pytest-cov

# Set up config
cp config.yaml config.yaml

# Initialize database
python -c "from backend.database import engine; from backend import models; models.Base.metadata.create_all(bind=engine)"

# Run development server
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

## Project Structure

```
legacy/
├── backend/
│   ├── main.py              # FastAPI app entry point
│   ├── config.py            # Configuration management
│   ├── database.py          # Database engine & sessions
│   ├── models.py            # SQLAlchemy ORM models
│   ├── schemas.py           # Pydantic request/response schemas
│   ├── auth.py              # Authentication & authorization
│   ├── exceptions.py        # Error handling
│   ├── logging_config.py    # Structured logging
│   ├── routers/             # HTTP route handlers
│   ├── services/            # Business logic layer
│   └── utils/               # Utility functions
├── templates/               # Jinja2 HTML templates
├── static/                  # CSS, JS assets
├── tests/                   # Test suite
├── docs/                    # Documentation
├── config.yaml              # Configuration
├── Dockerfile
└── docker-compose.yml
```

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=backend --cov-report=term-missing

# Specific test file
pytest tests/test_auth.py -v
```

## Coding Standards

- Follow PEP 8
- Use type hints
- Services should not import from routers
- Routers should be thin - logic in services
- All new features need tests
- All endpoints need OpenAPI documentation

## Creating a New Endpoint

1. Define schema in `schemas.py`
2. Add service function in appropriate `services/` file
3. Create route handler in `routers/`
4. Add auth/permission checks
5. Add audit logging
6. Register router in `main.py`
7. Write tests in `tests/`
8. Update OpenAPI tags if needed

## Database Migrations

```bash
# The schema is auto-created by SQLAlchemy on startup.
# For migration scripts, use backend/migrate_v3.sql.
```

## Adding New Models

1. Define class in `models.py`
2. Add Pydantic schema in `schemas.py`
3. Create service functions
4. Run migration script if altering existing tables

## Configuration

The `config.yaml` file is loaded at startup. Settings can also be overridden via environment variables:

```python
from backend.config import config
database_url = config.database["url"]
log_level = config.logging["level"]
```

## Adding Background Jobs

1. Add handler in `services/queue_service.py` `_execute_job()`
2. Register job type in `JOB_TYPES`
3. Enqueue via `queue.enqueue("job_type", payload)`

## Security Guidelines

- Never log passwords or secrets
- Always check permissions on every endpoint
- Use parameterized queries (SQLAlchemy handles this)
- Validate all input with Pydantic schemas
- Rate limiting via reverse proxy
- Keep dependencies updated
