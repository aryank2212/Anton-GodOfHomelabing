-- LEGACY v3.0.0 Migration
-- Adds authentication, audit, jobs, backups tables
-- Adds indexes, constraints, and cascading rules

BEGIN TRANSACTION;

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(128) NOT NULL UNIQUE,
    email VARCHAR(256),
    password_hash VARCHAR(256) NOT NULL,
    role VARCHAR(32) NOT NULL DEFAULT 'user',
    is_active INTEGER NOT NULL DEFAULT 1,
    display_name VARCHAR(256),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME,
    last_login_at DATETIME
);
CREATE INDEX IF NOT EXISTS ix_users_username ON users(username);

-- Sessions table
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id VARCHAR(128) NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ip_address VARCHAR(45),
    user_agent VARCHAR(512),
    expires_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_accessed_at DATETIME
);
CREATE INDEX IF NOT EXISTS ix_sessions_session_id ON sessions(session_id);
CREATE INDEX IF NOT EXISTS ix_sessions_user_id ON sessions(user_id);

-- API Keys table
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_hash VARCHAR(256) NOT NULL UNIQUE,
    name VARCHAR(128) NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(32) NOT NULL DEFAULT 'agent',
    is_active INTEGER NOT NULL DEFAULT 1,
    last_used_at DATETIME,
    expires_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_api_keys_key_hash ON api_keys(key_hash);

-- Add user_id and is_pinned to entries
ALTER TABLE entries ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE entries ADD COLUMN is_pinned INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS ix_entries_user_id ON entries(user_id);
CREATE INDEX IF NOT EXISTS ix_entries_visibility ON entries(visibility);
CREATE INDEX IF NOT EXISTS ix_entries_source ON entries(source);
CREATE INDEX IF NOT EXISTS ix_entries_entry_type ON entries(entry_type);
CREATE INDEX IF NOT EXISTS ix_entries_tags ON entries(tags);

-- Add unique constraint to entities
-- SQLite doesn't support ALTER TABLE ADD CONSTRAINT, so we handle this in the model

-- EntryEntities index
CREATE INDEX IF NOT EXISTS ix_entry_entities_entry_id ON entry_entities(entry_id);
CREATE INDEX IF NOT EXISTS ix_entry_entities_entity_id ON entry_entities(entity_id);

-- CollectionEntries index
CREATE INDEX IF NOT EXISTS ix_collection_entries_collection_id ON collection_entries(collection_id);
CREATE INDEX IF NOT EXISTS ix_collection_entries_entry_id ON collection_entries(entry_id);

-- Add user_id to collections
ALTER TABLE collections ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE collections ADD COLUMN visibility VARCHAR(32) NOT NULL DEFAULT 'private';
ALTER TABLE collections ADD COLUMN updated_at DATETIME;
CREATE INDEX IF NOT EXISTS ix_collections_user_id ON collections(user_id);

-- Add user_id to events
ALTER TABLE events ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS ix_events_created_at ON events(created_at);
CREATE INDEX IF NOT EXISTS ix_events_source_event_type ON events(source, event_type);

-- Add weight and constraints to links
ALTER TABLE links ADD COLUMN weight FLOAT DEFAULT 1.0;
CREATE INDEX IF NOT EXISTS ix_links_source ON links(source_type, source_id);
CREATE INDEX IF NOT EXISTS ix_links_target ON links(target_type, target_id);

-- Add updated_at to settings
ALTER TABLE settings ADD COLUMN updated_at DATETIME;
-- Note: SQLite does not support ALTER COLUMN; value is already TEXT

-- Audit Logs table
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action VARCHAR(64) NOT NULL,
    actor_type VARCHAR(32) NOT NULL,
    actor_id INTEGER,
    actor_name VARCHAR(256),
    resource_type VARCHAR(64) NOT NULL,
    resource_id INTEGER,
    details TEXT,
    ip_address VARCHAR(45),
    user_agent VARCHAR(512),
    duration_ms INTEGER,
    status VARCHAR(16) NOT NULL DEFAULT 'success',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_audit_logs_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS ix_audit_logs_actor ON audit_logs(actor_type, actor_id);
CREATE INDEX IF NOT EXISTS ix_audit_logs_resource ON audit_logs(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS ix_audit_logs_created_at ON audit_logs(created_at);

-- Jobs table (background queue)
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 0,
    payload TEXT,
    result TEXT,
    error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    scheduled_at DATETIME,
    started_at DATETIME,
    completed_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_jobs_status_type ON jobs(status, job_type);
CREATE INDEX IF NOT EXISTS ix_jobs_scheduled ON jobs(status, scheduled_at);

-- Backups table
CREATE TABLE IF NOT EXISTS backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename VARCHAR(512) NOT NULL,
    size_bytes INTEGER,
    checksum VARCHAR(128),
    backup_type VARCHAR(32) NOT NULL DEFAULT 'full',
    includes VARCHAR(512),
    status VARCHAR(16) NOT NULL DEFAULT 'completed',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

COMMIT;
