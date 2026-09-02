-- Phase 5 Migration: Add Anton Ecosystem columns to entries table
-- Run with: sqlite3 database/legacy.db < backend/migrate_phase5.sql

ALTER TABLE entries ADD COLUMN visibility VARCHAR DEFAULT 'private';
ALTER TABLE entries ADD COLUMN source VARCHAR DEFAULT 'journal';
ALTER TABLE entries ADD COLUMN metadata_json TEXT;

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source VARCHAR NOT NULL,
    event_type VARCHAR NOT NULL,
    severity VARCHAR DEFAULT 'info',
    title VARCHAR,
    description TEXT,
    metadata_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_events_source ON events (source);
CREATE INDEX IF NOT EXISTS ix_events_event_type ON events (event_type);

CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type VARCHAR NOT NULL,
    source_id INTEGER NOT NULL,
    target_type VARCHAR NOT NULL,
    target_id VARCHAR NOT NULL,
    relationship VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_links_id ON links (id);
