# LEGACY API Documentation

## Base URL

```
http://localhost:8000
```

Interactive documentation available at:
- Swagger UI: `/api/docs`
- ReDoc: `/api/redoc`
- OpenAPI JSON: `/api/openapi.json`

## Authentication

### Cookie-based Sessions (Browser)

```
POST /api/auth/login
Content-Type: application/json

{
    "username": "admin",
    "password": "admin"
}

Response:
{
    "session_id": "abc123...",
    "user": { ... },
    "expires_at": "2024-01-01T00:00:00"
}
```

The `session_id` is set as a cookie and sent automatically.

### API Key Authentication (Agents)

```
POST /api/auth/apikeys
Cookie: session_id=...

{
    "name": "my-agent",
    "role": "agent"
}

Response:
{
    "id": 1,
    "name": "my-agent",
    "key": "leg_abc123...",
    ...
}
```

Use the key as a Bearer token:

```
Authorization: Bearer leg_abc123...
```

## Roles

| Role | Permissions |
|------|-------------|
| `admin` | Full access to everything |
| `user` | Read/write own entries, read public |
| `agent` | API access for automated agents |
| `read-only` | Read access only |

## Endpoints

### Authentication

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/register` | Create new user |
| POST | `/api/auth/login` | Login, get session |
| POST | `/api/auth/logout` | Logout, destroy session |
| GET | `/api/auth/me` | Get current user |
| GET | `/api/auth/sessions` | List active sessions |
| DELETE | `/api/auth/sessions/{id}` | Delete session |
| POST | `/api/auth/apikeys` | Create API key |
| GET | `/api/auth/apikeys` | List API keys |
| DELETE | `/api/auth/apikeys/{id}` | Delete API key |

### Entries

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/entries/` | Create entry |
| GET | `/api/entries/` | List entries |
| GET | `/api/entries/{id}` | Get entry |
| PUT | `/api/entries/{id}` | Update entry |
| DELETE | `/api/entries/{id}` | Delete entry |

### Memory

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/memory` | Create memory |
| GET | `/api/memory/search` | Search memories |
| GET | `/api/memory/timeline` | Get memory timeline |
| GET | `/api/memory/{id}` | Get memory |
| DELETE | `/api/memory/{id}` | Delete memory |
| POST | `/api/memory/reflection` | Create reflection |
| POST | `/api/memory/entity` | Store entity |
| POST | `/api/memory/embed` | Generate embedding |
| POST | `/api/memory/observation` | Store observation |

### AI

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/ai/graph` | Knowledge graph data |
| GET | `/api/ai/similar/{id}` | Similar memories |
| GET | `/api/ai/related/{id}` | Related entries |
| GET | `/api/ai/search` | Semantic search |
| GET | `/api/ai/status` | Embedding status |
| GET | `/api/ai/entities` | List entities |
| GET | `/api/ai/entities/{id}` | Get entity with entries |
| GET | `/api/ai/collections` | List collections |
| GET | `/api/ai/collections/{id}` | Get collection |
| GET | `/api/ai/settings` | Get settings |
| POST | `/api/ai/settings` | Update settings |
| POST | `/api/ai/collections/create` | Create collection |

### Knowledge

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/knowledge/search` | Cross-source search |
| GET | `/api/knowledge/context` | Topic context |
| GET | `/api/knowledge/rag` | RAG context |
| GET | `/api/knowledge/summary` | Entry summary |

### Events

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/events` | Publish event |
| GET | `/api/events` | List events |
| GET | `/api/events/stats` | Event statistics |
| GET | `/api/events/{id}` | Get event |
| DELETE | `/api/events/{id}` | Delete event |

### Admin

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/admin/users` | List users |
| GET | `/api/admin/users/{id}` | Get user |
| PUT | `/api/admin/users/{id}` | Update user |
| DELETE | `/api/admin/users/{id}` | Delete user |
| GET | `/api/admin/audit` | Audit log |
| GET | `/api/admin/audit/stats` | Audit statistics |
| POST | `/api/admin/backup` | Create backup |
| GET | `/api/admin/backups` | List backups |
| POST | `/api/admin/backups/restore` | Restore backup |
| GET | `/api/admin/queue` | Queue status |
| POST | `/api/admin/queue/jobs` | Create job |
| GET | `/api/admin/system/version` | System version |

### Export / Import

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/export/export` | Export data (json/csv/markdown) |
| POST | `/api/export/import` | Import data (json/csv) |

### Health

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/ready` | Readiness check |
| GET | `/live` | Liveness check |
| GET | `/status` | Full status |

## Visibility Levels

| Level | Description |
|-------|-------------|
| `private` | Only the owner and admins |
| `shared` | Any authenticated user |
| `public` | Anyone (no auth required) |
| `agent-only` | Authenticated users only |
| `system` | Admins only |

## Response Format

Success:
```json
{
    "id": 1,
    "title": "...",
    ...
}
```

Error:
```json
{
    "error": {
        "code": "NOT_FOUND",
        "message": "Entry not found",
        "details": {}
    }
}
```

## Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `NOT_FOUND` | 404 | Resource not found |
| `UNAUTHORIZED` | 401 | Authentication required |
| `FORBIDDEN` | 403 | Insufficient permissions |
| `VALIDATION_ERROR` | 400/422 | Invalid input |
| `CONFLICT` | 409 | Duplicate resource |
| `RATE_LIMITED` | 429 | Too many requests |
| `INTERNAL_ERROR` | 500 | Server error |
| `SERVICE_UNAVAILABLE` | 503 | Service unavailable |
