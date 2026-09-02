"""Execution engine: decision -> approval -> execution, with gates."""

from __future__ import annotations

from app.config import Settings
from app.engine import ExecutionEngine
from app.policy import Policy, PolicyConfig, Preapproval
from app.schemas import ToolCall
from app.tools.registry import ToolRegistry
from tests.conftest import FakeTool


def _engine(policy: Policy, audit, approvals, hermes, settings=None) -> ExecutionEngine:
    registry = ToolRegistry(
        [
            FakeTool("docker_restart"),
            FakeTool("docker_rollback", risk="high"),
            FakeTool("docker_ps", read_only=True),
            FakeTool("docker_stop", risk="medium"),
        ]
    )
    return ExecutionEngine(
        registry=registry,
        policy=policy,
        approvals=approvals,
        audit=audit,
        settings=settings
        or Settings(_env_file=None, token="test", config_file="nope", environment="test"),
        hermes=hermes,
    )


def _auto_policy() -> Policy:
    return Policy.from_config(
        PolicyConfig(
            autonomy_level=2,
            preapproved=[Preapproval(tool="docker_restart", target="*", max_per_hour=5)],
        ),
        read_only_tools={"docker_ps"},
    )


def _approval_policy() -> Policy:
    return Policy.from_config(PolicyConfig(autonomy_level=1), read_only_tools={"docker_ps"})


async def test_read_only_runs_at_any_level(engine, hermes, audit) -> None:
    response = await engine.call(ToolCall(tool="docker_ps", args={}))
    assert response.ok is True
    assert response.decision == "allowed"
    assert not hermes.requested
    rows = await audit.recent()
    assert len(rows) == 1
    assert rows[0]["decision"] == "allowed"


async def test_level_1_act_tool_creates_approval(engine, hermes, approvals) -> None:
    response = await engine.call(
        ToolCall(tool="docker_restart", args={"target": "gitea"}, reason="down")
    )
    assert response.decision == "approval"
    assert response.approval_id
    assert hermes.requested
    assert hermes.requested[0][0] == response.approval_id
    assert len(await approvals.pending()) == 1


async def test_duplicate_pending_approval_rejected(engine, hermes) -> None:
    await engine.call(ToolCall(tool="docker_restart", args={"target": "gitea"}))
    second = await engine.call(ToolCall(tool="docker_restart", args={"target": "gitea"}))
    assert second.ok is False
    assert "already pending" in second.output
    assert len(hermes.requested) == 1


async def test_resolve_approved_executes(engine, hermes, audit) -> None:
    pending = await engine.call(ToolCall(tool="docker_restart", args={"target": "gitea"}))
    response = await engine.resolve(pending.approval_id, True)
    assert response is not None
    assert response.ok is True
    assert response.decision == "approval"
    assert hermes.events[0]["type"] == "forge.action.ok"
    rows = await audit.recent()
    assert rows[1]["decision"] == "approval"
    assert rows[1]["ok"] is True


async def test_resolve_rejected_does_not_run(engine, hermes, approvals) -> None:
    pending = await engine.call(ToolCall(tool="docker_restart", args={"target": "gitea"}))
    response = await engine.resolve(pending.approval_id, False)
    assert response is None
    assert hermes.events[0]["type"] == "forge.action.rejected"
    assert await approvals.pending() == []


async def test_hermes_unreachable_expires_approval(engine, hermes, approvals) -> None:
    hermes.approve = False
    response = await engine.call(ToolCall(tool="docker_restart", args={"target": "gitea"}))
    assert response.ok is False
    assert "cannot reach Hermes" in response.output
    assert await approvals.pending() == []


async def test_level_2_auto_runs_without_approval(audit, approvals, hermes) -> None:
    engine = _engine(_auto_policy(), audit, approvals, hermes)
    response = await engine.call(ToolCall(tool="docker_restart", args={"target": "gitea"}))
    assert response.ok is True
    assert response.decision == "auto"
    assert not hermes.requested
    rows = await audit.recent()
    assert rows[0]["decision"] == "auto"


async def test_level_2_non_preapproved_goes_to_approval(audit, approvals, hermes) -> None:
    engine = _engine(_auto_policy(), audit, approvals, hermes)
    response = await engine.call(ToolCall(tool="docker_stop", args={"target": "gitea"}))
    assert response.decision == "approval"
    assert response.approval_id


async def test_level_0_blocks_act_tools(audit, approvals, hermes) -> None:
    policy = Policy.from_config(PolicyConfig(autonomy_level=0), read_only_tools={"docker_ps"})
    engine = _engine(policy, audit, approvals, hermes)
    response = await engine.call(ToolCall(tool="docker_restart", args={"target": "gitea"}))
    assert response.decision == "blocked"
    assert response.ok is False
    assert not hermes.requested


async def test_unknown_tool(audit, approvals, hermes) -> None:
    engine = _engine(_auto_policy(), audit, approvals, hermes)
    response = await engine.call(ToolCall(tool="docker_sudo", args={}))
    assert response.ok is False
    assert "unknown tool" in response.output


async def test_auto_cooldown_escalates_to_approval(audit, approvals, hermes) -> None:
    engine = _engine(_auto_policy(), audit, approvals, hermes)
    first = await engine.call(ToolCall(tool="docker_restart", args={"target": "gitea"}))
    assert first.decision == "auto"
    second = await engine.call(ToolCall(tool="docker_restart", args={"target": "gitea"}))
    assert second.decision == "approval"
    assert "cooldown" in second.output


async def test_crashloop_escalates_to_approval(audit, approvals, hermes) -> None:
    policy = Policy.from_config(
        PolicyConfig(
            autonomy_level=2,
            preapproved=[Preapproval(tool="docker_restart", target="*")],
            cooldowns={"target_seconds": 0.0, "crashloop_threshold": 3},
        ),
        read_only_tools={"docker_ps"},
    )
    engine = _engine(policy, audit, approvals, hermes)
    for _ in range(3):
        response = await engine.call(ToolCall(tool="docker_restart", args={"target": "gitea"}))
        assert response.decision == "auto"
    fourth = await engine.call(ToolCall(tool="docker_restart", args={"target": "gitea"}))
    assert fourth.decision == "approval"
    assert "crash-looping" in fourth.output


async def test_rate_limit_escalates_to_approval(audit, approvals, hermes) -> None:
    policy = Policy.from_config(
        PolicyConfig(
            autonomy_level=2,
            preapproved=[Preapproval(tool="docker_restart", target="*", max_per_hour=2)],
            cooldowns={"target_seconds": 0.0, "crashloop_threshold": 99},
        ),
        read_only_tools={"docker_ps"},
    )
    engine = _engine(policy, audit, approvals, hermes)
    await engine.call(ToolCall(tool="docker_restart", args={"target": "a"}))
    await engine.call(ToolCall(tool="docker_restart", args={"target": "a"}))
    third = await engine.call(ToolCall(tool="docker_restart", args={"target": "a"}))
    assert third.decision == "approval"
    assert "rate limit" in third.output


async def test_tool_error_is_caught_and_audited(audit, approvals, hermes) -> None:
    registry = ToolRegistry([FakeTool("boom", ok=False)])
    policy = Policy.from_config(
        PolicyConfig(
            autonomy_level=2,
            preapproved=[Preapproval(tool="boom", target="*")],
        ),
        read_only_tools=set(),
    )
    engine = ExecutionEngine(
        registry=registry,
        policy=policy,
        approvals=approvals,
        audit=audit,
        settings=Settings(_env_file=None, token="test", config_file="nope", environment="test"),
        hermes=hermes,
    )
    response = await engine.call(ToolCall(tool="boom", args={}))
    assert response.ok is False
    assert hermes.events[0]["type"] == "forge.action.failed"
