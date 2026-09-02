# LEGACY Architecture

## Overview

LEGACY is a production-grade memory service for the Anton ecosystem. It provides persistent storage, semantic search, knowledge graphs, and agent integration through a FastAPI-based REST API.

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Client / Agent                         │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTP/WS
┌──────────────────────▼──────────────────────────────────┐
│                    FastAPI App                            │
│  ┌─────────┐ ┌──────────┐ ┌─────────┐ ┌──────────────┐ │
│  │ Auth    │ │ Routers  │ │ Services│ │ Background    │ │
│  │ Module  │ │          │ │ Layer   │ │ Queue         │ │
│  └────┬────┘ └────┬─────┘ └────┬────┘ └──────┬───────┘ │
│       │           │            │              │          │
│  ┌────▼───────────▼────────────▼──────────────▼───────┐ │
│  │                  SQLAlchemy ORM                      │ │
│  └──────────────────────┬──────────────────────────────┘ │
└─────────────────────────┼────────────────────────────────┘
                          │
             ┌────────────▼────────────┐
             │      SQLite / PG         │
             └─────────────────────────┘
```

## Components

### 1. FastAPI Application (`backend/main.py`)
- Central application entry point
- Middleware stack (CORS, request ID, timing)
- Router registration
- OpenAPI schema generation
- Startup/shutdown lifecycle

### 2. Router Layer (`backend/routers/`)
Thin HTTP layer. Each router handles a domain:

| Router | Prefix | Purpose |
|--------|--------|---------|
| `auth.py` | `/api/auth` | Login, logout, sessions, API keys |
| `entries.py` | `/api/entries` | Journal entry CRUD |
| `memory.py` | `/api/memory` | Memory operations, search |
| `ai.py` | `/api/ai` | AI features: graph, similarity, entities |
| `knowledge.py` | `/api/knowledge` | Knowledge search, RAG, context |
| `events.py` | `/api/events` | System event pub/sub |
| `admin.py` | `/api/admin` | Admin: users, audit, backup, queue |
| `health.py` | (none) | `/health`, `/ready`, `/live` |
| `export.py` | `/api/export` | Export/import |
| `pages.py` | (none) | Server-rendered HTML pages |

### 3. Service Layer (`backend/services/`)
Business logic isolated from HTTP:

- **`memory_service.py`** - Core memory CRUD, embedding pipeline
- **`embedding_service.py`** - Sentence-transformers integration
- **`entity_service.py`** - Named entity extraction
- **`retrieval_service.py`** - Similarity and related entry search
- **`graph_service.py`** - Knowledge graph construction
- **`knowledge_service.py`** - Cross-source knowledge search
- **`rag_service.py`** - RAG context assembly
- **`event_service.py`** - Event pub/sub
- **`agent_service.py`** - Agent abstraction layer
- **`dashboard.py`** - Dashboard data aggregation
- **`analytics.py`** - Writing analytics computation
- **`streaks.py`** - Journaling streak calculation
- **`audit_service.py`** - Audit logging
- **`queue_service.py`** - Background job queue
- **`backup_service.py`** - Export, import, backup, restore

### 4. Auth Module (`backend/auth.py`)
- Password hashing (PBKDF2-HMAC-SHA256)
- Session management with cookies/headers
- API key authentication (Bearer tokens)
- Role-based access control (admin, user, agent, read-only)
- Visibility enforcement (private, shared, public, agent-only, system)

### 5. Background Queue (`backend/services/queue_service.py`)
- Priority-based job queue with SQLite persistence
- Worker threads for parallel processing
- Automatic retry with configurable delay
- Job types: embedding, reflection, entity extraction, summaries, reindexing, cleanup

### 6. Database (`backend/database.py`)
- SQLAlchemy ORM with SQLite (default) or PostgreSQL support
- WAL mode, foreign keys, connection pooling
- Health check support

## Data Flow

```
Request → Middleware → Router → Auth Check → Service → Database
                                                          │
                                                     Queue Job
                                                          │
                                                    Background
                                                    Processing
```

## Security

- **Authentication**: Session cookies for browser, API keys for agents
- **Authorization**: Role-based (admin, user, agent, read-only)
- **Visibility**: Per-entry permissions (private, shared, public, agent-only, system)
- **Audit**: All operations logged with actor, resource, timestamp
- **CSRF**: Session-based auth with SameSite cookies
