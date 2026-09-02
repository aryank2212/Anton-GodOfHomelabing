"""Docker tools: the container control surface Forge exposes to the agent.

Read-only tools (``docker_ps``, ``docker_inspect``, ``docker_logs``,
``docker_stats``) are always allowed. Act tools declare their own risk and the
policy decides whether they auto-run, need approval, or are refused.
"""

from __future__ import annotations

import json
from typing import Any

from app.clients.docker import (
    DockerGateway,
    DockerUnavailableError,
    run_in_thread,
    tool_result_error,
)
from app.state import StateManager
from app.tools.base import Tool, ToolResult, ToolSpec, target_schema

_TARGET_DESC = "container name (e.g. 'gitea' or 'ant-hermes')"


def _fmt(data: Any, indent: int = 2) -> str:
    try:
        return json.dumps(data, indent=indent, default=str)
    except (TypeError, ValueError):
        return str(data)


class DockerPs(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="docker_ps",
            description=(
                "List all Docker containers with name, image, status, running "
                "flag and health. Use this to discover which container a "
                "failing service maps to."
            ),
            risk="low",
            read_only=True,
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )

    def __init__(self, gateway: DockerGateway) -> None:
        self._gateway = gateway

    async def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            rows = await run_in_thread(self._gateway.ps)
        except DockerUnavailableError as exc:
            return tool_result_error(str(exc))
        return ToolResult(ok=True, output=_fmt(rows), data={"containers": rows})


class DockerInspect(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="docker_inspect",
            description=(
                "Inspect a container: full state, health, restart policy, "
                "env keys, mounts and port bindings."
            ),
            risk="low",
            read_only=True,
            parameters=target_schema(_TARGET_DESC),
        )

    def __init__(self, gateway: DockerGateway) -> None:
        self._gateway = gateway

    def identity(self, args: dict[str, Any]) -> str:
        return str(args.get("target") or "?")

    async def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            data = await run_in_thread(self._gateway.inspect, args["target"])
        except DockerUnavailableError as exc:
            return tool_result_error(str(exc))
        return ToolResult(ok=True, output=_fmt(data), data=data)


class DockerLogs(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="docker_logs",
            description=(
                "Tail a container's logs (default 100 lines, timestamps). "
                "The fastest way to see why a service is failing."
            ),
            risk="low",
            read_only=True,
            parameters={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": _TARGET_DESC,
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$",
                    },
                    "tail": {
                        "type": "integer",
                        "description": "number of lines to fetch (1..500)",
                        "minimum": 1,
                        "maximum": 500,
                    },
                },
                "required": ["target"],
                "additionalProperties": False,
            },
        )

    def __init__(self, gateway: DockerGateway) -> None:
        self._gateway = gateway

    def identity(self, args: dict[str, Any]) -> str:
        return str(args.get("target") or "?")

    async def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            logs = await run_in_thread(
                self._gateway.logs, args["target"], int(args.get("tail", 100))
            )
        except DockerUnavailableError as exc:
            return tool_result_error(str(exc))
        return ToolResult(ok=True, output=logs or "(no logs)", data={"logs": logs})


class DockerStats(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="docker_stats",
            description=(
                "Live CPU and memory usage for a container. Useful to tell "
                "whether a service is spinning or memory-starved."
            ),
            risk="low",
            read_only=True,
            parameters=target_schema(_TARGET_DESC),
        )

    def __init__(self, gateway: DockerGateway) -> None:
        self._gateway = gateway

    def identity(self, args: dict[str, Any]) -> str:
        return str(args.get("target") or "?")

    async def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            data = await run_in_thread(self._gateway.stats, args["target"])
        except DockerUnavailableError as exc:
            return tool_result_error(str(exc))
        return ToolResult(ok=True, output=_fmt(data), data=data)


class _DockerLifecycle(Tool):
    """Shared skeleton for restart / start / stop (their bodies only differ in
    the verb and risk)."""

    name = "docker_restart"
    description = "restart"
    risk = "low"
    verb = "restart"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=f"{self.description} a container by name.",
            risk=self.risk,
            parameters=target_schema(_TARGET_DESC),
        )

    def __init__(self, gateway: DockerGateway) -> None:
        self._gateway = gateway

    def identity(self, args: dict[str, Any]) -> str:
        return str(args.get("target") or "?")

    async def run(self, args: dict[str, Any]) -> ToolResult:
        target = args["target"]
        try:
            await run_in_thread(getattr(self._gateway, self.verb), target)
        except DockerUnavailableError as exc:
            return tool_result_error(str(exc))
        return ToolResult(
            ok=True, output=f"{self.description} {target} ok", data={"target": target}
        )


class DockerRestart(_DockerLifecycle):
    name = "docker_restart"
    description = "restart"
    verb = "restart"
    risk = "low"


class DockerStart(_DockerLifecycle):
    name = "docker_start"
    description = "start"
    verb = "start"
    risk = "low"


class DockerStop(_DockerLifecycle):
    name = "docker_stop"
    description = "stop"
    verb = "stop"
    risk = "medium"


class DockerMarkGood(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="docker_mark_good",
            description=(
                "Record the container's current image as its last-known-good "
                "tag. Call this after a service is verified healthy so "
                "docker_rollback has a safe baseline. Medium risk: only "
                "affects the rollback bookkeeping, not the running service."
            ),
            risk="medium",
            parameters=target_schema(_TARGET_DESC),
        )

    def __init__(self, gateway: DockerGateway, state: StateManager) -> None:
        self._gateway = gateway
        self._state = state

    def identity(self, args: dict[str, Any]) -> str:
        return str(args.get("target") or "?")

    async def run(self, args: dict[str, Any]) -> ToolResult:
        target = args["target"]
        try:
            image = await run_in_thread(self._gateway.current_image, target)
        except DockerUnavailableError as exc:
            return tool_result_error(str(exc))
        if not image:
            return tool_result_error(f"{target}: no image tag to record")
        await self._state.set_known_good_image(target, image)
        return ToolResult(
            ok=True,
            output=f"known-good image for {target} -> {image}",
            data={"target": target, "image": image},
        )


class DockerRollback(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="docker_rollback",
            description=(
                "Recreate a container from its last-known-good image (recorded "
                "by docker_mark_good), preserving env, mounts, ports and "
                "network. The current container is renamed (not deleted) so "
                "the rollback is reversible. High risk: recreates a running "
                "container and causes downtime."
            ),
            risk="high",
            parameters=target_schema(_TARGET_DESC),
        )

    def __init__(self, gateway: DockerGateway, state: StateManager) -> None:
        self._gateway = gateway
        self._state = state

    def identity(self, args: dict[str, Any]) -> str:
        return str(args.get("target") or "?")

    async def run(self, args: dict[str, Any]) -> ToolResult:
        target = args["target"]
        image = self._state.known_good_image(target)
        if not image:
            return tool_result_error(
                f"{target}: no known-good image recorded (use docker_mark_good)"
            )
        try:
            await run_in_thread(self._gateway.rollback_to_image, target, image)
        except DockerUnavailableError as exc:
            return tool_result_error(str(exc))
        return ToolResult(
            ok=True,
            output=f"rolled {target} back to {image}",
            data={"target": target, "image": image},
        )


class DockerScale(Tool):
    """Scale a Compose service to N replicas.

    Uses the host's docker compose (v2) against the allow-listed project's
    compose file. Only projects and services listed in forge.yaml may be
    scaled.
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="docker_scale",
            description=(
                "Scale a Compose service to N replicas. Takes a project name "
                "and service name that must be in Forge's allow-list, and a "
                "replica count 0..8. 0 stops the service."
            ),
            risk="medium",
            parameters={
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "allow-listed compose project name",
                        "pattern": "^[A-Za-z0-9_.-]+$",
                    },
                    "service": {
                        "type": "string",
                        "description": "service name inside that project",
                        "pattern": "^[A-Za-z0-9_.-]+$",
                    },
                    "replicas": {"type": "integer", "minimum": 0, "maximum": 8},
                },
                "required": ["project", "service", "replicas"],
                "additionalProperties": False,
            },
        )

    def __init__(self, policy, runner: Any | None = None) -> None:
        self._policy = policy
        self._runner = runner  # async fn(command, cwd) -> (ok, output); injected in tests

    def identity(self, args: dict[str, Any]) -> str:
        return f"{args.get('project')}/{args.get('service')}"

    async def run(self, args: dict[str, Any]) -> ToolResult:
        project = args["project"]
        service = args["service"]
        replicas = int(args["replicas"])
        spec = self._policy.resolve_project(project)
        if spec is None:
            return tool_result_error(f"unknown compose project '{project}' (not allow-listed)")
        if service not in spec.services:
            return tool_result_error(
                f"service '{service}' is not allow-listed in project '{project}'"
            )
        compose_file = f"{spec.path.rstrip('/')}/docker-compose.yml"
        command = (
            f"docker compose -f {compose_file} up -d --no-recreate " f"--scale {service}={replicas}"
        )
        if self._runner is not None:
            ok, output = await self._runner(command, spec.path)
        else:
            ok, output = await self._default_runner(command, spec.path)
        if not ok:
            return tool_result_error(output or "docker compose scale failed")
        return ToolResult(
            ok=True,
            output=output or f"{project}/{service} scaled to {replicas}",
            data={"project": project, "service": service, "replicas": replicas},
        )

    async def _default_runner(self, command: str, cwd: str) -> tuple[bool, str]:
        import asyncio

        process = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=120)
        except TimeoutError:
            process.kill()
            await process.communicate()
            return False, "docker compose scale timed out after 120s"
        output = stdout.decode(errors="replace").strip()
        return process.returncode in (0, None), output
