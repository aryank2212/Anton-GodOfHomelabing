"""Watcher API — exposes WatchYourLAN device intelligence.

Read layer over the watcher Postgres database. Applies human-readable device
aliases (aliases.json) on top of raw records, offers a compact ``/summary``
for AI consumers (Sentinel / Oracle) and is health-checked by Sentinel's
watcher observer.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras
from fastapi import FastAPI

_ALIASES_FILE = Path(os.environ.get("WATCHER_ALIASES_FILE", "/app/aliases.json"))
DB_CONFIG = {
    "host": os.environ.get("WATCHER_DB_HOST", "localhost"),
    "port": int(os.environ.get("WATCHER_DB_PORT", "5438")),
    "dbname": os.environ.get("WATCHER_DB_NAME", "watcher"),
    "user": os.environ.get("WATCHER_DB_USER", "watcher"),
    "password": os.environ.get("WATCHER_DB_PASSWORD", "watcher123"),
}


def load_aliases() -> dict[str, str]:
    try:
        return json.loads(_ALIASES_FILE.read_text())
    except (OSError, ValueError):
        return {}


def alias_for(aliases: dict[str, str], mac: str, name: str) -> str:
    """Return the human-readable alias for a MAC (falling back to the name)."""
    key = (mac or "").lower()
    if key in aliases:
        return aliases[key]
    return name


def connect() -> Any:
    return psycopg2.connect(**DB_CONFIG)


app = FastAPI()


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "Watcher", "status": "online"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/aliases")
def aliases() -> dict[str, str]:
    return load_aliases()


@app.get("/devices")
def devices() -> list[dict[str, Any]]:
    aliases_map = load_aliases()
    conn = connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT mac, name, ip, online, last_seen
                FROM devices
                ORDER BY last_seen DESC
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {
            "mac": row["mac"],
            "name": alias_for(aliases_map, row["mac"], row["name"]),
            "ip": row["ip"],
            "online": row["online"],
            "last_seen": row["last_seen"],
        }
        for row in rows
    ]


@app.get("/events")
def events() -> list[dict[str, Any]]:
    conn = connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT event, timestamp
                FROM events
                ORDER BY timestamp DESC
                LIMIT 100
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


@app.get("/summary")
def summary() -> dict[str, Any]:
    """Compact fleet overview sized for AI context (Sentinel / Oracle)."""
    aliases_map = load_aliases()
    conn = connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT count(*) AS n FROM devices WHERE online")
            online = cur.fetchone()["n"]
            cur.execute("SELECT count(*) AS n FROM devices")
            total = cur.fetchone()["n"]
            cur.execute(
                """
                SELECT mac, name, ip, online, last_seen
                FROM devices
                ORDER BY last_seen DESC
                LIMIT 50
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return {
        "total_devices": total,
        "online_devices": online,
        "devices": [
            {
                "name": alias_for(aliases_map, row["mac"], row["name"]),
                "ip": row["ip"],
                "mac": row["mac"],
                "online": row["online"],
            }
            for row in rows
        ],
    }
