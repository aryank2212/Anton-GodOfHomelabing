from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class RuleAction(StrEnum):
    IGNORE = "ignore"
    LOG = "log"
    NOTIFY = "notify"
    REMEDIATE = "remediate"


class Remediation(BaseModel):
    """What to do when a rule fires. ``kind`` selects the action.

    * ``http`` — call an arbitrary endpoint (URL and JSON body are Jinja2
      templated with the event context). Useful to hit e.g. the Portainer API.
    * ``command`` — run a shell command on the Hermes host.
    * ``docker_restart`` — ``docker restart <container>`` (requires the docker
      CLI and access to the docker socket from the Hermes runtime).

    All remediation is gated behind ``HERMES_REMEDIATION_ENABLED``.
    """

    kind: Literal["http", "command", "docker_restart"]
    url: str | None = None
    method: Literal["get", "post", "put", "delete"] = "post"
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = None
    command: str | None = None
    container: str | None = None
    timeout: float = Field(default=30.0, gt=0, le=120)

    @model_validator(mode="after")
    def _validate_kind(self) -> Remediation:
        if self.kind == "http" and not self.url:
            raise ValueError("http remediation requires a url")
        if self.kind == "command" and not self.command:
            raise ValueError("command remediation requires a command")
        if self.kind == "docker_restart" and not self.container:
            raise ValueError("docker_restart remediation requires a container")
        return self


class Rule(BaseModel):
    """A single rule.

    ``when`` maps event fields to expected values. A value may be a plain
    scalar (compared for equality, with fnmatch wildcards supported on
    strings), a list (the event value must be one of the listed values), or,
    for the ``tags`` field, a string or list matched against the event tags.
    """

    name: str = Field(min_length=1)
    action: RuleAction
    when: dict[str, Any] = Field(default_factory=dict)
    providers: list[str] = Field(default_factory=list)
    remediation: Remediation | None = None

    @model_validator(mode="after")
    def _validate(self) -> Rule:
        if self.action is RuleAction.NOTIFY and not self.providers:
            raise ValueError(f"rule '{self.name}' has action 'notify' but no providers configured")
        if self.action is RuleAction.REMEDIATE and self.remediation is None:
            raise ValueError(
                f"rule '{self.name}' has action 'remediate' but no remediation configured"
            )
        return self


class RulesFile(BaseModel):
    """Top-level YAML document for the rule configuration."""

    version: int = 1
    rules: list[Rule] = Field(default_factory=list)
