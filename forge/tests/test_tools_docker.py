"""Docker tools against a fake gateway — no daemon needed."""

from __future__ import annotations

import pytest

from app.policy import Policy, PolicyConfig
from app.state import StateManager
from app.tools.docker_tools import (
    DockerLogs,
    DockerMarkGood,
    DockerPs,
    DockerRestart,
    DockerRollback,
    DockerScale,
    DockerStart,
    DockerStop,
)
from tests.conftest import FakeGateway


@pytest.fixture
def gateway():
    return FakeGateway()


async def test_docker_ps(gateway) -> None:
    result = await DockerPs(gateway).run({})
    assert result.ok
    assert result.data["containers"][0]["name"] == "gitea"


async def test_docker_logs(gateway) -> None:
    result = await DockerLogs(gateway).run({"target": "gitea", "tail": 50})
    assert result.ok
    assert "all good" in result.output


async def test_docker_restart(gateway) -> None:
    result = await DockerRestart(gateway).run({"target": "gitea"})
    assert result.ok
    assert gateway.restarted == ["gitea"]


async def test_docker_start_stop(gateway) -> None:
    assert (await DockerStart(gateway).run({"target": "gitea"})).ok
    assert (await DockerStop(gateway).run({"target": "gitea"})).ok
    assert gateway.started == ["gitea"]
    assert gateway.stopped == ["gitea"]


async def test_docker_mark_good(gateway, tmp_path) -> None:
    state = StateManager(str(tmp_path / "state.json"))
    result = await DockerMarkGood(gateway, state).run({"target": "gitea"})
    assert result.ok
    assert state.known_good_image("gitea") == "gitea/gitea:latest"


async def test_docker_rollback_without_known_good(gateway, tmp_path) -> None:
    state = StateManager(str(tmp_path / "state.json"))
    result = await DockerRollback(gateway, state).run({"target": "gitea"})
    assert result.ok is False
    assert "no known-good image" in result.error


async def test_docker_rollback_with_known_good(gateway, tmp_path) -> None:
    state = StateManager(str(tmp_path / "state.json"))
    await state.set_known_good_image("gitea", "gitea/gitea:1.21.0")
    result = await DockerRollback(gateway, state).run({"target": "gitea"})
    assert result.ok
    assert "1.21.0" in result.output


async def test_docker_scale_allow_list_and_runner() -> None:
    policy = Policy.from_config(
        PolicyConfig(
            compose_projects=[
                {"name": "homepage", "path": "/tmp/homepage", "services": ["homepage", "db"]}
            ]
        ),
        read_only_tools=set(),
    )
    calls: list[tuple[str, str]] = []

    async def runner(command: str, cwd: str) -> tuple[bool, str]:
        calls.append((command, cwd))
        return True, "created homepage"

    tool = DockerScale(policy, runner=runner)
    assert (
        tool.identity({"project": "homepage", "service": "homepage", "replicas": 2})
        == "homepage/homepage"
    )

    result = await tool.run({"project": "homepage", "service": "homepage", "replicas": 2})
    assert result.ok
    assert "docker compose" in calls[0][0]
    assert "--scale homepage=2" in calls[0][0]

    unknown_project = await tool.run({"project": "immich", "service": "immich", "replicas": 2})
    assert unknown_project.ok is False
    assert "not allow-listed" in unknown_project.error

    unknown_service = await tool.run({"project": "homepage", "service": "postgres", "replicas": 2})
    assert unknown_service.ok is False
    assert "not allow-listed" in unknown_service.error
