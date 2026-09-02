"""Policy is the staged-autonomy gate: pure, deterministic, fail-closed."""

from __future__ import annotations

from pathlib import Path

from app.policy import (
    ComposeProject,
    ManagedRepo,
    Policy,
    PolicyConfig,
    Preapproval,
    load_policy_config,
)


def test_level_0_refuses_act_tools() -> None:
    policy = Policy.from_config(PolicyConfig(autonomy_level=0), read_only_tools={"docker_ps"})
    assert policy.decide("docker_ps", "low", True, "gitea").decision == "allowed"
    assert policy.decide("docker_restart", "low", False, "gitea").decision == "blocked"


def test_level_1_requires_approval_for_act_tools() -> None:
    policy = Policy.from_config(PolicyConfig(autonomy_level=1), read_only_tools={"docker_ps"})
    assert policy.decide("docker_ps", "low", True, "gitea").decision == "allowed"
    assert policy.decide("docker_restart", "low", False, "gitea").decision == "approval"


def test_level_2_auto_runs_preapproved_low_risk() -> None:
    policy = Policy.from_config(
        PolicyConfig(
            autonomy_level=2,
            preapproved=[Preapproval(tool="docker_restart", target="gitea")],
        ),
        read_only_tools={"docker_ps"},
    )
    assert policy.decide("docker_restart", "low", False, "gitea").decision == "auto"
    assert policy.decide("docker_restart", "low", False, "immich").decision == "approval"


def test_level_2_high_risk_needs_force() -> None:
    policy = Policy.from_config(
        PolicyConfig(
            autonomy_level=2,
            preapproved=[Preapproval(tool="docker_rollback", target="*")],
        ),
        read_only_tools=set(),
    )
    assert policy.decide("docker_rollback", "high", False, "gitea").decision == "approval"
    forced = Policy.from_config(
        PolicyConfig(
            autonomy_level=2,
            preapproved=[Preapproval(tool="docker_rollback", target="*", force=True)],
        ),
        read_only_tools=set(),
    )
    assert forced.decide("docker_rollback", "high", False, "gitea").decision == "auto"


def test_target_glob_matching() -> None:
    policy = Policy.from_config(
        PolicyConfig(
            autonomy_level=2,
            preapproved=[Preapproval(tool="docker_start", target="homepage*")],
        ),
        read_only_tools=set(),
    )
    assert policy.decide("docker_start", "low", False, "homepage").decision == "auto"
    assert policy.decide("docker_start", "low", False, "gitea").decision == "approval"


def test_repo_resolution_by_name_and_path() -> None:
    policy = Policy.from_config(
        PolicyConfig(managed_repos=[ManagedRepo(path="/opt/anton/phoenix")]),
        read_only_tools=set(),
    )
    assert policy.is_allowed_repo("/opt/anton/phoenix")
    assert policy.is_allowed_repo("phoenix")
    assert not policy.is_allowed_repo("/opt/anton/hermes")
    assert policy.resolve_repo("/etc/passwd") is None


def test_project_service_allow_list() -> None:
    policy = Policy.from_config(
        PolicyConfig(
            compose_projects=[ComposeProject(name="homepage", path="/x", services=["homepage"])]
        ),
        read_only_tools=set(),
    )
    assert policy.is_allowed_project_service("homepage", "homepage")
    assert not policy.is_allowed_project_service("homepage", "db")
    assert policy.resolve_project("nope") is None


def test_missing_config_fails_closed(tmp_path: Path) -> None:
    config = load_policy_config(str(tmp_path / "does-not-exist.yaml"))
    assert config.autonomy_level == 0


def test_duplicate_repos_rejected(tmp_path: Path) -> None:
    config_file = tmp_path / "dupe.yaml"
    config_file.write_text("managed_repos:\n  - path: /a/b\n  - path: /a/b\n", encoding="utf-8")
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        load_policy_config(str(config_file))
