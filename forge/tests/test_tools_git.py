"""Git tools against a real throwaway repo — no host access needed."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.policy import ManagedRepo, Policy, PolicyConfig
from app.state import StateManager
from app.tools.git_tools import GitMarkGood, GitRollback, GitStatus

REPO_REF = "forge-known-good"


def _git(repo: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path) -> str:
    root = tmp_path / "repo"
    root.mkdir()
    _git(str(root), "init", "-b", "main")
    _git(str(root), "config", "user.email", "test@anton.local")
    _git(str(root), "config", "user.name", "Forge Test")
    (root / "app.py").write_text("print('v1')\n", encoding="utf-8")
    _git(str(root), "add", ".")
    _git(str(root), "commit", "-m", "initial")
    return str(root)


@pytest.fixture
def policy(repo) -> Policy:
    return Policy.from_config(
        PolicyConfig(managed_repos=[ManagedRepo(path=repo)]),
        read_only_tools={"git_status", "git_log"},
    )


def _commit_broken(repo: str) -> None:
    Path(repo, "app.py").write_text("print('v2 broken')\n", encoding="utf-8")
    _git(repo, "commit", "-am", "v2 broken")


async def test_git_status(policy, repo, tmp_path) -> None:
    state = StateManager(str(tmp_path / "state.json"))
    result = await GitStatus(policy, state).run({"repo": repo})
    assert result.ok
    assert "initial" in result.output


async def test_git_status_blocks_unmanaged_repo(policy, tmp_path) -> None:
    state = StateManager(str(tmp_path / "state.json"))
    result = await GitStatus(policy, state).run({"repo": "/etc"})
    assert result.ok is False
    assert "allow-list" in result.error


async def test_git_mark_good_and_rollback(policy, repo, tmp_path) -> None:
    state = StateManager(str(tmp_path / "state.json"))
    marked = await GitMarkGood(policy, state).run({"repo": repo})
    assert marked.ok
    assert state.known_good_git(repo)

    _commit_broken(repo)
    assert "v2 broken" in _git(repo, "log", "-1", "--oneline")

    rolled = await GitRollback(policy, state).run({"repo": repo})
    assert rolled.ok
    assert "v1" in Path(repo, "app.py").read_text(encoding="utf-8")


async def test_git_rollback_without_mark_good(policy, repo, tmp_path) -> None:
    state = StateManager(str(tmp_path / "state.json"))
    result = await GitRollback(policy, state).run({"repo": repo})
    assert result.ok is False
    assert "no forge-known-good tag" in result.error


async def test_git_rollback_runs_deploy(policy, repo, tmp_path) -> None:
    state = StateManager(str(tmp_path / "state.json"))
    await GitMarkGood(policy, state).run({"repo": repo})
    calls: list[tuple[str, str]] = []

    async def runner(command: str, cwd: str) -> tuple[bool, str]:
        calls.append((command, cwd))
        return True, "deployed"

    policy.config.managed_repos[0].deploy = "./deploy.sh"
    tool = GitRollback(policy, state, runner=runner)
    result = await tool.run({"repo": repo})
    assert result.ok
    assert calls == [("./deploy.sh", repo)]
