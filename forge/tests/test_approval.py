"""Approval store lifecycle."""

from __future__ import annotations

from app.approval import ApprovalStore


async def test_create_and_resolve() -> None:
    store = ApprovalStore(timeout=600.0)
    approval = await store.create(
        tool="docker_restart",
        target="gitea",
        command="docker_restart gitea",
        risk="low",
        reason="test",
        caller="oracle",
        args={"target": "gitea"},
    )
    assert approval.state == "pending"
    assert len(await store.pending()) == 1

    resolved = await store.resolve(approval.id, True, by="telegram")
    assert resolved is not None
    assert resolved.state == "approved"
    assert resolved.resolved_by == "telegram"
    assert len(await store.pending()) == 0

    # double-resolve is a no-op
    assert await store.resolve(approval.id, False) is None


async def test_count_for_deduplicates_pending() -> None:
    store = ApprovalStore(timeout=600.0)
    await store.create(
        tool="docker_restart",
        target="gitea",
        command="c",
        risk="low",
        reason="r",
        caller="c",
        args={},
    )
    await store.create(
        tool="docker_restart",
        target="immich",
        command="c",
        risk="low",
        reason="r",
        caller="c",
        args={},
    )
    assert await store.count_for("docker_restart", "gitea") == 1
    assert await store.count_for("docker_restart", "immich") == 1


async def test_sweep_expires_stale_approvals() -> None:
    store = ApprovalStore(timeout=-1.0)  # already expired on creation
    approval = await store.create(
        tool="docker_restart",
        target="gitea",
        command="c",
        risk="low",
        reason="r",
        caller="c",
        args={},
    )
    expired = await store.sweep()
    assert [a.id for a in expired] == [approval.id]
    assert approval.state == "expired"
    assert await store.pending() == []
