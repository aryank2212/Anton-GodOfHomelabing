"""Shared fixtures for Forge tests.

Mirrors the phoenix/hermes convention: a ``settings`` fixture points at
throwaway paths, a ``app``/``client`` fixture pair boot the FastAPI app through
its real lifespan, and engine-level fixtures build a registry from fakes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.approval import ApprovalStore
from app.audit import AuditLog
from app.config import Settings
from app.engine import ExecutionEngine, HermesClient
from app.policy import (
    ComposeProject,
    Cooldowns,
    ManagedRepo,
    Policy,
    PolicyConfig,
    Preapproval,
)
from app.state import StateManager
from app.tools.base import Tool, ToolResult, ToolSpec
from app.tools.registry import ToolRegistry

PROJECT_ROOT = Path(__file__).resolve().parent.parent

MINIMAL_POLICY = """\
autonomy_level: 1
managed_repos:
  - path: {repo}
compose_projects:
  - name: homepage
    path: {proj}
    services: [homepage]
preapproved:
  - tool: docker_restart
    target: "*"
    max_per_hour: 5
cooldowns:
  target_seconds: 900
  crashloop_threshold: 3
  crashloop_window_seconds: 3600
"""


def write_policy(tmp_path: Path, level: int = 1) -> str:
    repo = tmp_path / "repo"
    proj = tmp_path / "compose"
    repo.mkdir(exist_ok=True)
    proj.mkdir(exist_ok=True)
    body = MINIMAL_POLICY.format(repo=repo, proj=proj)
    body = body.replace("autonomy_level: 1", f"autonomy_level: {level}")
    config_file = tmp_path / "forge.yaml"
    config_file.write_text(body, encoding="utf-8")
    return str(config_file)


def make_policy(
    level: int = 2,
    *,
    preapproved: list[Preapproval] | None = None,
    cooldowns: Cooldowns | None = None,
) -> Policy:
    config = PolicyConfig(
        autonomy_level=level,
        managed_repos=[ManagedRepo(path="/opt/anton/phoenix")],
        compose_projects=[
            ComposeProject(
                name="homepage", path="/opt/anton/docker/homepage", services=["homepage"]
            )
        ],
        preapproved=preapproved or [Preapproval(tool="docker_restart", target="*", max_per_hour=5)],
        cooldowns=cooldowns or Cooldowns(target_seconds=3600, crashloop_threshold=3),
    )
    return Policy.from_config(config, read_only_tools={"docker_ps"})


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings with throwaway state/audit paths and a working policy file."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]  # pydantic-settings init kwarg
        token="test-token",
        environment="test",
        config_file=write_policy(tmp_path, level=1),
        state_file=str(tmp_path / "forge-state.json"),
        audit_file=str(tmp_path / "forge-audit.jsonl"),
        hermes_url="http://hermes:8000",
        phoenix_url="http://phoenix:8010",
        watcher_url="http://host.docker.internal:8008",
        approval_timeout=600.0,
    )


@pytest.fixture
async def app(settings):
    from app.main import create_app

    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


class FakeHermes(HermesClient):
    def __init__(self) -> None:  # noqa: D107 - skip super() httpx client
        self.requested: list[tuple[str, str]] = []
        self.events: list[dict] = []
        self.approve = True

    async def request_approval(self, approval_id: str, text: str) -> bool:
        self.requested.append((approval_id, text))
        return self.approve

    async def publish_event(self, *, type_, severity, title, message="", metadata=None) -> None:
        self.events.append(
            {
                "type": type_,
                "severity": severity,
                "title": title,
                "message": message,
                "metadata": metadata,
            }
        )

    async def close(self) -> None:
        return None


class FakeGateway:
    """Stand-in for DockerGateway: records actions, returns canned rows."""

    def __init__(self) -> None:
        self.restarted: list[str] = []
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.images: dict[str, str] = {}

    def ps(self) -> list[dict]:
        return [
            {"name": "gitea", "image": "gitea/gitea:latest", "status": "running", "running": True}
        ]

    def inspect(self, target: str) -> dict:
        return {"Name": f"/{target}", "State": {"Status": "running"}}

    def logs(self, target: str, tail: int = 100) -> str:
        return f"[{target}] all good\n"

    def stats(self, target: str) -> dict:
        return {"cpu_percent": 1.0, "mem_percent": 2.0}

    def current_image(self, target: str) -> str | None:
        return self.images.get(target, "gitea/gitea:latest")

    def rollback_to_image(self, target: str, image: str) -> None:
        self.images[target] = image

    def restart(self, target: str) -> None:
        self.restarted.append(target)

    def start(self, target: str) -> None:
        self.started.append(target)

    def stop(self, target: str) -> None:
        self.stopped.append(target)


class FakeTool(Tool):
    """Configurable tool for engine tests."""

    def __init__(
        self, name: str, *, risk: str = "low", read_only: bool = False, ok: bool = True
    ) -> None:
        self._name = name
        self._risk = risk
        self._read_only = read_only
        self._ok = ok
        self.calls: list[dict] = []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self._name,
            description=f"fake {self._name}",
            risk=self._risk,
            read_only=self._read_only,
        )

    def identity(self, args: dict) -> str:
        return str(args.get("target") or args.get("repo") or "?")

    async def run(self, args: dict) -> ToolResult:
        self.calls.append(args)
        return ToolResult(ok=self._ok, output=f"{self._name} ran")


@pytest.fixture
def policy():
    return make_policy()


@pytest.fixture
def state(tmp_path: Path):
    return StateManager(str(tmp_path / "forge-state.json"))


@pytest.fixture
def audit(tmp_path: Path):
    return AuditLog(str(tmp_path / "forge-audit.jsonl"))


@pytest.fixture
def approvals():
    return ApprovalStore(timeout=600.0)


@pytest.fixture
def hermes():
    return FakeHermes()


@pytest.fixture
def engine(state, audit, approvals, hermes):
    registry = ToolRegistry(
        [
            FakeTool("docker_restart"),
            FakeTool("docker_mark_good", risk="medium"),
            FakeTool("docker_rollback", risk="high"),
            FakeTool("docker_ps", read_only=True),
            FakeTool("phoenix_recover", risk="medium"),
        ]
    )
    return ExecutionEngine(
        registry=registry,
        policy=make_policy(level=1),
        approvals=approvals,
        audit=audit,
        settings=Settings(_env_file=None, token="test", config_file="nope", environment="test"),
        hermes=hermes,
    )
