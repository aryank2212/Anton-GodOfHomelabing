from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.schemas import ToolSpecOut


@dataclass(frozen=True)
class ToolResult:
    """Outcome of one tool execution, as returned to the model / caller."""

    ok: bool
    output: str = ""
    data: dict[str, Any] | None = None
    error: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risk: str
    read_only: bool = False
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_out(self) -> ToolSpecOut:
        return ToolSpecOut(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            risk=self.risk,
            read_only=self.read_only,
        )


class Tool(ABC):
    """A single capability exposed by Forge.

    ``identity`` returns the target key used for pre-approval matching,
    cooldowns, crash-loop detection and rate limiting (e.g. a container name,
    ``project/service`` or an allowed repo path).
    """

    @property
    @abstractmethod
    def spec(self) -> ToolSpec: ...

    @abstractmethod
    async def run(self, args: dict[str, Any]) -> ToolResult: ...

    def identity(self, args: dict[str, Any]) -> str:
        return str(args.get("target") or "?")


def target_schema(
    description: str, pattern: str = "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
) -> dict[str, Any]:
    """Common JSON-schema fragment for tools that take a single ``target``."""
    return {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": description,
                "pattern": pattern,
            }
        },
        "required": ["target"],
        "additionalProperties": False,
    }
