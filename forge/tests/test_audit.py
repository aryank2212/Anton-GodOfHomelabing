"""Audit log: append-only JSONL, used for rate limiting and crash loops."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.audit import AuditLog


async def test_record_and_recent(tmp_path) -> None:
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    await audit.record({"tool": "docker_ps", "target": "gitea", "decision": "allowed", "ok": True})
    rows = await audit.recent()
    assert len(rows) == 1
    assert rows[0]["tool"] == "docker_ps"
    assert rows[0]["id"]


async def test_last_executed(tmp_path) -> None:
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    await audit.record({"tool": "docker_restart", "target": "gitea", "decision": "approval"})
    ts = await audit.last_executed(tool="docker_restart", target="gitea")
    assert ts is not None
    assert abs((datetime.now(UTC) - ts).total_seconds()) < 30
    assert await audit.last_executed(tool="docker_restart", target="immich") is None


async def test_count_recent_filters_decision_and_window(tmp_path) -> None:
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    await audit.record({"tool": "docker_restart", "target": "gitea", "decision": "auto", "ts": old})
    await audit.record({"tool": "docker_restart", "target": "gitea", "decision": "auto"})
    await audit.record({"tool": "docker_restart", "target": "gitea", "decision": "auto"})
    await audit.record({"tool": "docker_ps", "target": "gitea", "decision": "allowed"})
    assert await audit.count_recent(tool="docker_restart", target="gitea", seconds=3600) == 2
    assert await audit.count_recent(tool="docker_restart", target="gitea", seconds=3 * 3600) == 3
    # read-only executions are never counted toward limits
    assert await audit.count_recent(tool="docker_ps", target="gitea", seconds=3600) == 0


async def test_missing_audit_file(tmp_path) -> None:
    audit = AuditLog(str(tmp_path / "missing.jsonl"))
    assert await audit.recent() == []
