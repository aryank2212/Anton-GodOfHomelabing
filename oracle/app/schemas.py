from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Turn(BaseModel):
    """One message in the conversation history."""

    role: str = Field(pattern="^(user|assistant|system)$")
    content: str = Field(min_length=1, max_length=10_000)


class AskRequest(BaseModel):
    """Incoming question from Hermes (or any Tailnet client)."""

    message: str = Field(min_length=1, max_length=8_000)
    history: list[Turn] = Field(default_factory=list, max_length=50)
    # Optional live-state snapshot (e.g. "Hermes live state at ...") that is
    # injected as a system message before the conversation history.
    context: str | None = Field(default=None, max_length=12_000)


class AskResponse(BaseModel):
    """The model's answer plus generation metadata."""

    reply: str
    model: str
    tokens: dict[str, int] = Field(default_factory=dict)
    latency_ms: int = 0


class DecideRequest(BaseModel):
    """Situation snapshot for the watchdog decision endpoint."""

    situation: str = Field(min_length=1, max_length=12_000)


class DecideResponse(BaseModel):
    """Raw model reply (the JSON decision is parsed by the Hermes watchdog)."""

    reply: str
    model: str
    tokens: dict[str, int] = Field(default_factory=dict)
    latency_ms: int = 0


class HealthResponse(BaseModel):
    status: str
    model: str
    ollama: str


class AgentRequest(BaseModel):
    """User request routed through the tool-calling agent loop."""

    message: str = Field(min_length=1, max_length=8_000)
    history: list[Turn] = Field(default_factory=list, max_length=50)
    context: str | None = Field(default=None, max_length=12_000)


class AgentToolCall(BaseModel):
    """A strict JSON tool call emitted by the model."""

    tool: str = Field(min_length=1, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)


class AgentToolResult(BaseModel):
    """Outcome of one tool call the agent made through Forge."""

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    decision: str
    ok: bool | None = None
    output: str = ""
    error: str | None = None
    approval_id: str | None = None


class AgentResponse(BaseModel):
    """Final answer plus the tool calls the loop performed."""

    reply: str
    steps: int = 0
    tools: list[AgentToolResult] = Field(default_factory=list)
    model: str
    tokens: dict[str, int] = Field(default_factory=dict)
    latency_ms: int = 0
