"""Forge policy — the staged-autonomy gate.

Level 0 (diagnose-only): read-only tools only; every act tool is refused.
Level 1 (approval):       act tools require a human thumbs-up via Hermes →
                          Telegram (the ``approval`` decision).
Level 2 (auto):           act tools run automatically when their
                          ``(tool, target)`` pair is in ``preapproved`` AND
                          the tool risk is low (or the entry opts in with
                          ``force: true``); everything else still requires an
                          approval.

Safety is local and deterministic — it never depends on an LLM's judgement.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from app.schemas import Decision


class ComposeProject(BaseModel):
    name: str
    path: str
    services: list[str] = Field(default_factory=list)


class ManagedRepo(BaseModel):
    path: str
    deploy: str | None = None


class Preapproval(BaseModel):
    """A Level-2 auto-run allowance for a tool+target pair."""

    tool: str
    target: str = "*"
    max_per_hour: int = 3
    force: bool = False


class Cooldowns(BaseModel):
    target_seconds: float = 900.0
    crashloop_threshold: int = 3
    crashloop_window_seconds: float = 3600.0


class PolicyConfig(BaseModel):
    autonomy_level: int = Field(default=1, ge=0, le=2)
    managed_repos: list[ManagedRepo] = Field(default_factory=list)
    compose_projects: list[ComposeProject] = Field(default_factory=list)
    preapproved: list[Preapproval] = Field(default_factory=list)
    cooldowns: Cooldowns = Field(default_factory=Cooldowns)

    @field_validator("managed_repos")
    @classmethod
    def _unique_repos(cls, repos: list[ManagedRepo]) -> list[ManagedRepo]:
        seen: set[str] = set()
        for repo in repos:
            resolved = str(Path(repo.path).resolve())
            if resolved in seen:
                raise ValueError(f"duplicate managed repo: {repo.path}")
            seen.add(resolved)
        return repos

    @field_validator("compose_projects")
    @classmethod
    def _unique_projects(cls, projects: list[ComposeProject]) -> list[ComposeProject]:
        seen: set[str] = set()
        for project in projects:
            if project.name in seen:
                raise ValueError(f"duplicate compose project: {project.name}")
            seen.add(project.name)
        return projects


@dataclass
class PolicyDecision:
    """The outcome of policy evaluation for one tool call."""

    decision: Decision
    target: str = ""
    message: str = ""


@dataclass
class Policy:
    """Evaluates a tool call against the staged-autonomy configuration.

    Pure logic: all external state (rate limiting, cooldowns, crash loops) is
    evaluated by the engine and passed in via ``rate_ok``.
    """

    config: PolicyConfig
    read_only_tools: frozenset[str] = field(default_factory=frozenset)
    _repos_by_path: dict[str, ManagedRepo] = field(default_factory=dict)
    _projects_by_name: dict[str, ComposeProject] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: PolicyConfig, read_only_tools: set[str]) -> Policy:
        policy = cls(config=config, read_only_tools=frozenset(read_only_tools))
        policy._repos_by_path = {str(Path(r.path).resolve()): r for r in config.managed_repos}
        policy._projects_by_name = {p.name: p for p in config.compose_projects}
        return policy

    @property
    def autonomy_level(self) -> int:
        return self.config.autonomy_level

    def resolve_repo(self, raw: str) -> ManagedRepo | None:
        """Map a repo identifier (path or name) to a managed repo."""
        wanted = str(Path(raw).resolve()) if raw.startswith("/") else raw
        for repo in self.config.managed_repos:
            if repo.path == wanted:
                return repo
            if Path(repo.path).name == wanted:
                return repo
        return None

    def resolve_project(self, name: str) -> ComposeProject | None:
        return self._projects_by_name.get(name)

    def is_allowed_repo(self, raw: str) -> bool:
        return self.resolve_repo(raw) is not None

    def is_allowed_project_service(self, project: str, service: str) -> bool:
        spec = self._projects_by_name.get(project)
        return spec is not None and service in spec.services

    def decide(self, tool_name: str, risk: str, read_only: bool, target: str) -> PolicyDecision:
        """Classify one tool call: allowed / auto / approval / blocked."""
        if read_only:
            return PolicyDecision("allowed", target=target)

        if self.config.autonomy_level == 0:
            return PolicyDecision(
                "blocked",
                target=target,
                message="diagnose-only mode: act tools are disabled",
            )

        if self.config.autonomy_level == 1:
            return PolicyDecision("approval", target=target)

        entry = self._preapproval_for(tool_name, target)
        if entry is None:
            return PolicyDecision("approval", target=target)
        if risk != "low" and not entry.force:
            return PolicyDecision(
                "approval",
                target=target,
                message=f"{tool_name} is '{risk}' risk and is not force-approved",
            )
        return PolicyDecision("auto", target=target)

    def _preapproval_for(self, tool_name: str, target: str) -> Preapproval | None:
        for entry in self.config.preapproved:
            if entry.tool != tool_name:
                continue
            if fnmatch.fnmatch(target, entry.target):
                return entry
        return None


def load_policy_config(path: str) -> PolicyConfig:
    """Load and validate forge.yaml. A missing file fails closed at level 0."""
    p = Path(path)
    if not p.exists():
        return PolicyConfig(autonomy_level=0)
    data: Any = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return PolicyConfig.model_validate(data)
