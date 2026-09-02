from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Decision = Literal["allowed", "auto", "approval", "blocked"]
ApprovalState = Literal["pending", "approved", "rejected", "expired", "failed"]


class ToolSpecOut(BaseModel):
    """A tool as advertised to AI callers (Oracle's agent loop)."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    risk: str
    read_only: bool = False


class ToolCall(BaseModel):
    """A single tool invocation from a caller (Oracle agent, Hermes, tests)."""

    tool: str = Field(min_length=1, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=2000)


class ToolCallResponse(BaseModel):
    """Result of a tool invocation."""

    ok: bool
    tool: str
    output: str = ""
    data: dict[str, Any] | None = None
    error: str | None = None
    decision: Decision = "allowed"
    approval_id: str | None = None


class ApprovalOut(BaseModel):
    """A pending Level-1 approval."""

    id: str
    tool: str
    target: str
    command: str
    risk: str
    reason: str = ""
    caller: str = "unknown"
    created_at: datetime
    expires_at: datetime
    state: ApprovalState


class ApprovalRequest(BaseModel):
    """Hermes asks Forge to create a Telegram approval (mirror of Forge's own
    request to Hermes, used by the Hermes approval bridge)."""

    id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=2000)


class ApprovalResolve(BaseModel):
    """Operator's answer to a pending approval (sent by Hermes)."""

    approved: bool
    by: str = Field(default="telegram", max_length=64)


class RunOut(BaseModel):
    """One audit entry (tool execution attempt)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    timestamp: datetime = Field(alias="ts")
    tool: str
    args: dict[str, Any]
    caller: str
    decision: Decision
    ok: bool | None = None
    error: str | None = None
    output: str = ""
