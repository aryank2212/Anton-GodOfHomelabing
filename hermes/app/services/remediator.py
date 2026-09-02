from __future__ import annotations

import asyncio
import fnmatch
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.config.settings import Settings
from app.core.logging import get_logger
from app.core.renderer import Renderer
from app.rules.models import Remediation

log = get_logger(__name__)

#: Runs a shell command and returns its trimmed output. Injectable for tests.
CommandRunner = Callable[[str, float], Awaitable[str]]


class RemediationError(Exception):
    """Raised when a remediation action cannot be executed."""


@dataclass(frozen=True)
class RemediationResult:
    success: bool
    detail: str


async def _default_command(command: str, timeout: float) -> str:
    """Run ``command`` on the host and return its trimmed combined output."""
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise RemediationError(f"command timed out after {timeout:g}s") from None
    output = stdout.decode(errors="replace").strip()
    if process.returncode not in (0, None):
        raise RemediationError(f"command exited {process.returncode}: {output or '(no output)'}")
    return output


class Remediator:
    """Executes rule-triggered remediation actions.

    Everything is off by default: set ``HERMES_REMEDIATION_ENABLED=true`` to
    allow any remediation to run. ``command`` remediations may additionally be
    restricted with ``HERMES_REMEDIATION_ALLOWED_COMMANDS`` (a comma-separated
    list of fnmatch patterns).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        renderer: Renderer | None = None,
        client: httpx.AsyncClient | None = None,
        run_command: CommandRunner | None = None,
    ) -> None:
        self._settings = settings
        self._renderer = renderer
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=15.0)
        self._run_command = run_command or _default_command

    async def run(self, remediation: Remediation, event: dict[str, Any]) -> RemediationResult:
        if not self._settings.remediation_enabled:
            return RemediationResult(
                False, "remediation is disabled (set HERMES_REMEDIATION_ENABLED=true)"
            )
        try:
            if remediation.kind == "http":
                detail = await self._http(remediation, event)
            else:
                detail = await self._run_shell(remediation)
        except RemediationError as exc:
            log.warning(
                "remediation_failed",
                extra={"kind": remediation.kind, "error": str(exc), "event_id": event.get("id")},
            )
            return RemediationResult(False, str(exc))
        return RemediationResult(True, detail)

    async def _http(self, remediation: Remediation, event: dict[str, Any]) -> str:
        url = self._template(remediation.url, event)
        headers = {key: self._template(value, event) for key, value in remediation.headers.items()}
        body = self._template_body(remediation.body, event)
        try:
            response = await self._client.request(
                remediation.method.upper(),
                url,
                json=body if body is not None else None,
                headers=headers,
                timeout=remediation.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RemediationError(f"{remediation.method.upper()} {url} failed: {exc}") from exc
        return f"{remediation.method.upper()} {url} -> {response.status_code}"

    async def _run_shell(self, remediation: Remediation) -> str:
        if remediation.kind == "docker_restart":
            command = f"docker restart {shlex.quote(remediation.container or '')}"
        else:
            command = remediation.command or ""
        allowed = self._settings.remediation_command_patterns
        if allowed and not any(fnmatch.fnmatch(command, pattern) for pattern in allowed):
            raise RemediationError(
                f"command {command!r} is not allowed by HERMES_REMEDIATION_ALLOWED_COMMANDS"
            )
        output = await self._run_command(command, remediation.timeout)
        return output or "(no output)"

    def _template(self, value: str | None, event: dict[str, Any]) -> str:
        if value is None:
            return ""
        if self._renderer is not None:
            return self._renderer.render_text(value, event)
        return value

    def _template_body(self, body: Any, event: dict[str, Any]) -> Any:
        if isinstance(body, dict):
            return {key: self._template_body(value, event) for key, value in body.items()}
        if isinstance(body, list):
            return [self._template_body(value, event) for value in body]
        if isinstance(body, str) and self._renderer is not None:
            return self._renderer.render_text(body, event)
        return body

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
