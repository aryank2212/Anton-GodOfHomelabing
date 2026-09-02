"""Git tools: inspect and roll back the allow-listed repos on the host.

Only repos listed in ``managed_repos`` in forge.yaml are addressable. Paths
are never taken from model arguments — a repo is referenced by its name or by
an allow-listed path, and git runs with ``cwd`` inside that repo (no shell
interpolation), so an LLM cannot make git touch anything outside the list.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from app.policy import Policy
from app.state import StateManager
from app.tools.base import Tool, ToolResult, ToolSpec

KNOWN_GOOD_TAG = "forge-known-good"

_REPO_PARAM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "repo": {
            "type": "string",
            "description": "managed repository name or path (e.g. 'phoenix' or /opt/anton/phoenix)",
        }
    },
    "required": ["repo"],
    "additionalProperties": False,
}


async def _run_git(repo_path: str, args: list[str], timeout: float = 30.0) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        repo_path,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.communicate()
        return 1, f"git {args[0]} timed out after {timeout:.0f}s"
    return process.returncode or 0, stdout.decode(errors="replace").strip()


class _GitTool(Tool):
    def __init__(self, policy: Policy, state: StateManager) -> None:
        self._policy = policy
        self._state = state

    def _repo(self, args: dict[str, Any]) -> str | None:
        repo = self._policy.resolve_repo(str(args.get("repo") or ""))
        return repo.path if repo else None

    def identity(self, args: dict[str, Any]) -> str:
        repo = self._policy.resolve_repo(str(args.get("repo") or ""))
        return repo.path if repo else str(args.get("repo") or "?")


class GitStatus(_GitTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="git_status",
            description=(
                "Status of a managed repo: current branch/commit and dirty "
                "state. Repo must be in Forge's allow-list."
            ),
            risk="low",
            read_only=True,
            parameters=_REPO_PARAM,
        )

    async def run(self, args: dict[str, Any]) -> ToolResult:
        path = self._repo(args)
        if path is None:
            return ToolResult(
                ok=False,
                error="repo not in managed allow-list",
                output="repo not in managed allow-list",
            )
        code, status = await _run_git(path, ["status", "--porcelain=v1", "--branch"])
        code2, head = await _run_git(path, ["log", "-1", "--oneline"])
        if code != 0:
            return ToolResult(ok=False, error=status, output=status)
        output = f"{status}\nHEAD: {head}"
        return ToolResult(
            ok=True, output=output, data={"repo": path, "status": status, "head": head}
        )


class GitLog(_GitTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="git_log",
            description=(
                "Recent commit history of a managed repo (oneline). Useful to "
                "correlate a failing deploy with the last change."
            ),
            risk="low",
            read_only=True,
            parameters={
                "type": "object",
                "properties": {
                    "repo": _REPO_PARAM["properties"]["repo"],
                    "count": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["repo"],
                "additionalProperties": False,
            },
        )

    async def run(self, args: dict[str, Any]) -> ToolResult:
        path = self._repo(args)
        if path is None:
            return ToolResult(
                ok=False,
                error="repo not in managed allow-list",
                output="repo not in managed allow-list",
            )
        count = int(args.get("count", 15))
        code, log = await _run_git(path, ["log", "--oneline", "-n", str(count)])
        if code != 0:
            return ToolResult(ok=False, error=log, output=log)
        return ToolResult(ok=True, output=log, data={"repo": path, "log": log})


class GitMarkGood(_GitTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="git_mark_good",
            description=(
                "Tag the current HEAD of a managed repo as its last-known-good "
                "ref (tag 'forge-known-good'). Call after a deploy is verified "
                "so git_rollback has a safe baseline. Medium risk: only moves "
                "a local tag."
            ),
            risk="medium",
            parameters=_REPO_PARAM,
        )

    async def run(self, args: dict[str, Any]) -> ToolResult:
        path = self._repo(args)
        if path is None:
            return ToolResult(
                ok=False,
                error="repo not in managed allow-list",
                output="repo not in managed allow-list",
            )
        code, head = await _run_git(path, ["rev-parse", "HEAD"])
        if code != 0:
            return ToolResult(ok=False, error=head, output=head)
        # Move the tag to the current HEAD (force so re-marking is idempotent).
        code, _ = await _run_git(path, ["tag", "-f", KNOWN_GOOD_TAG, head])
        if code != 0:
            return ToolResult(ok=False, error="could not move tag", output="could not move tag")
        await self._state.set_known_good_git(path, head)
        return ToolResult(
            ok=True,
            output=f"marked {path} HEAD {head[:12]} as known-good",
            data={"repo": path, "ref": head},
        )


class GitRollback(_GitTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="git_rollback",
            description=(
                "Roll a managed repo back to its last-known-good ref (tag "
                "'forge-known-good', set by git_mark_good): check out that "
                "commit and optionally run the repo's configured deploy "
                "command. High risk: changes deployed code and can redeploy."
            ),
            risk="high",
            parameters=_REPO_PARAM,
        )

    def __init__(self, policy: Policy, state: StateManager, runner: Any | None = None) -> None:
        super().__init__(policy, state)
        self._runner = runner  # async fn(command, cwd) -> (ok, output); injected in tests

    async def run(self, args: dict[str, Any]) -> ToolResult:
        raw = str(args.get("repo") or "")
        repo = self._policy.resolve_repo(raw)
        if repo is None:
            return ToolResult(
                ok=False,
                error="repo not in managed allow-list",
                output="repo not in managed allow-list",
            )
        code, _ = await _run_git(repo.path, ["checkout", KNOWN_GOOD_TAG])
        if code != 0:
            return ToolResult(
                ok=False,
                error=f"no {KNOWN_GOOD_TAG} tag (run git_mark_good after a verified deploy)",
                output=f"no {KNOWN_GOOD_TAG} tag (run git_mark_good after a verified deploy)",
            )
        message = f"{repo.path}: checked out {KNOWN_GOOD_TAG}"
        if repo.deploy:
            if self._runner is not None:
                ok, output = await self._runner(repo.deploy, repo.path)
            else:
                ok, output = await self._default_runner(repo.deploy, repo.path)
            if not ok:
                return ToolResult(
                    ok=False,
                    error=f"deploy command failed: {output}",
                    output=f"{message}\ndeploy failed: {output}",
                )
            message += f"\ndeploy: {output or 'ok'}"
        return ToolResult(ok=True, output=message, data={"repo": repo.path, "ref": KNOWN_GOOD_TAG})

    async def _default_runner(self, command: str, cwd: str) -> tuple[bool, str]:
        env = dict(os.environ)
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=180)
        except TimeoutError:
            process.kill()
            await process.communicate()
            return False, "deploy command timed out after 180s"
        output = stdout.decode(errors="replace").strip()
        return process.returncode in (0, None), output
