# LEGACY Memory System

## Overview

LEGACY is a persistent memory system designed to serve as the core storage layer for the Anton ecosystem. It provides:

- **Journal entries** with rich metadata (type, mood, tags)
- **Vector embeddings** for semantic search
- **Knowledge graphs** connecting related memories
- **Entity extraction** and tracking
- **Background processing** for embeddings and reflections
- **Agent integration** with access control

## Core Concepts

### Entry Types

| Type | Description |
|------|-------------|
| Journal | Daily journal entries |
| Poetry | Poetic writings |
| Letter To Future Self | Letters to future self |
| Chronicle | Historical records |
| Philosophy Essay | Philosophical writings |
| Engineering Log | Technical logs |
| Dream | Dream recordings |
| Idea | Idea captures |
| Reflection | Self-reflections |
| Observation | System observations |

### Visibility Levels

| Level | Description |
|-------|-------------|
| `private` | Only visible to owner and admins |
| `shared` | Visible to all authenticated users |
| `public` | Visible to everyone |
| `agent-only` | Visible to authenticated users/agents |
| `system` | Visible to admins only |

### Embedding Pipeline

1. Entry is created
2. Background job queued for embedding generation
3. Text is encoded using sentence-transformers (all-MiniLM-L6-v2)
4. Vector stored in database as JSON
5. Semantic search uses cosine similarity

### Entity Extraction

When entries are created, the system automatically extracts:
- Known technologies (Python, Docker, Kubernetes, etc.)
- Quoted phrases
- Proper nouns (capitalized words)
- Person names (multi-capitalized phrases)

### Knowledge Graph

The graph is built from:
- Shared tags between entries
- Shared entities between entries
- Edge weights based on shared connections

## Agent Integration

Agents (watcher, hermes, sentinel, phoenix) can:

| Operation | Description |
|-----------|-------------|
| `read_memory()` | Read memories with visibility check |
| `write_memory()` | Create new memories |
| `search_memory()` | Search across keyword and semantic |
| `reflect()` | Generate reflections |

Agents authenticate via API keys:

```python
Authorization: Bearer leg_<api_key>
```

## Search Capabilities

### Keyword Search
- Searches title, content, tags, mood
- Supports filtering by source, type, visibility
- Paginated results

### Semantic Search
- Uses sentence embeddings for meaning-based search
- Cosine similarity ranking (threshold: 0.2)
- Combined with keyword results

### Hybrid Search
- Agent search combines keyword + semantic
- Deduplicated results

## Background Jobs

| Job Type | Description | Schedule |
|----------|-------------|----------|
| Embedding | Generate missing embeddings | On-demand + queue |
| Reflection | Daily reflection generation | 23:00 daily |
| Entity Extraction | Extract entities from text | On creation |
| Summary | Generate entry summaries | On creation |
| Reindex | Regenerate all embeddings | Manual |
| Cleanup | Remove old audit logs | Periodic |
| Session Cleanup | Remove expired sessions | Periodic |
