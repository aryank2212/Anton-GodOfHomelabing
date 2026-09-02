"""Database access layer.

Currently backed by SQLite (via aiosqlite). The schema and query code are
written against SQLAlchemy 2.0 so that swapping in PostgreSQL later only
requires changing ``HERMES_DATABASE_URL`` (and installing the matching driver).
"""
