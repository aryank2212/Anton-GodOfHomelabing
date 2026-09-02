from __future__ import annotations

import json
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.config import Settings, get_settings
from app.forge import ForgeClient, ForgeError
from app.ollama import OllamaClient, OllamaError
from app.schemas import (
    AgentRequest,
    AgentResponse,
    AgentToolCall,
    AgentToolResult,
    AskRequest,
    AskResponse,
    DecideRequest,
    DecideResponse,
    HealthResponse,
)


def create_app(
    settings: Settings | None = None,
    ollama: OllamaClient | None = None,
    forge: ForgeClient | None = None,
) -> FastAPI:
    """Application factory. ``settings`` / ``ollama`` / ``forge`` are injectable
    for tests."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        instance = ollama or OllamaClient(settings)
        forge_instance = forge or ForgeClient(settings)
        application.state.ollama = instance
        application.state.forge = forge_instance
        application.state.settings = settings
        try:
            yield
        finally:
            await instance.close()
            await forge_instance.close()

    application = FastAPI(
        title="Anton Oracle",
        description=(
            "AI gateway for Anton. Runs on the Laptop and is consumed over "
            "Tailscale by Hermes; the server never loads models itself."
        ),
        version=__version__,
        lifespan=lifespan,
    )

    async def _require_auth(authorization: str | None = Header(default=None)) -> None:
        if not settings.shared_token:
            return
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="unauthorized")
        provided = authorization[len("Bearer ") :].strip()
        if not secrets.compare_digest(provided, settings.shared_token):
            raise HTTPException(status_code=401, detail="unauthorized")

    @application.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> JSONResponse:
        instance: OllamaClient = request.app.state.ollama
        return JSONResponse(
            {
                "status": "ok",
                "model": settings.model,
                "ollama": "up" if await instance.is_up() else "down",
            }
        )

    @application.post("/v1/ask", response_model=AskResponse, dependencies=[Depends(_require_auth)])
    async def ask(payload: AskRequest, request: Request) -> JSONResponse:
        instance: OllamaClient = request.app.state.ollama
        history = payload.history[-settings.max_history :]
        messages: list[dict[str, Any]] = [{"role": "system", "content": settings.system_prompt}]
        if payload.context:
            messages.append({"role": "system", "content": payload.context})
        messages.extend({"role": turn.role, "content": turn.content} for turn in history)
        messages.append({"role": "user", "content": payload.message})
        try:
            content, metadata = await instance.chat(messages)
        except OllamaError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return JSONResponse(
            {
                "reply": content,
                "model": metadata["model"],
                "tokens": metadata["tokens"],
                "latency_ms": metadata["latency_ms"],
            }
        )

    @application.post(
        "/v1/agent", response_model=AgentResponse, dependencies=[Depends(_require_auth)]
    )
    async def agent(payload: AgentRequest, request: Request) -> JSONResponse:
        """Tool-calling agent loop backed by the Forge execution layer.

        The model answers with plain text (final answer) or a strict JSON tool
        call. Tool calls are relayed to Forge, which enforces policy and Level-1
        approvals; the result is fed back to the model for up to
        ``agent_max_steps`` rounds. The gateway itself never executes anything.
        """
        if not settings.forge_url or not settings.forge_token:
            raise HTTPException(status_code=503, detail="forge not configured")
        instance: OllamaClient = request.app.state.ollama
        forge_instance: ForgeClient = request.app.state.forge

        started = time.monotonic()
        try:
            catalog = await forge_instance.list_tools()
        except ForgeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        known_tools = {tool["name"] for tool in catalog if isinstance(tool.get("name"), str)}

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": settings.agent_prompt.replace("{tools}", json.dumps(catalog, indent=2)),
            }
        ]
        if payload.context:
            messages.append({"role": "system", "content": payload.context})
        messages.extend(
            {"role": turn.role, "content": turn.content}
            for turn in payload.history[-settings.max_history :]
        )
        messages.append({"role": "user", "content": payload.message})

        steps = 0
        results: list[AgentToolResult] = []
        reply = ""
        metadata: dict[str, Any] = {}
        forced = False
        while True:
            try:
                content, metadata = await instance.chat(
                    messages, temperature=settings.agent_temperature
                )
            except OllamaError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            content = content.strip()
            call = _parse_tool_call(content)
            if call is None:
                reply = content
                break
            messages.append({"role": "assistant", "content": content})
            if steps >= settings.agent_max_steps or _over_budget(
                messages, settings.agent_context_budget
            ):
                if forced:
                    reply = content
                    break
                forced = True
                messages.append(
                    {
                        "role": "user",
                        "content": "You have reached the tool-call or context "
                        "limit. Give your final answer to the user now in plain "
                        "text, without calling more tools.",
                    }
                )
                continue
            steps += 1
            if call.tool not in known_tools:
                result = AgentToolResult(
                    tool=call.tool,
                    args=call.args,
                    decision="blocked",
                    ok=False,
                    error=f"unknown tool '{call.tool}'",
                )
            else:
                try:
                    data = await forge_instance.run(
                        call.tool,
                        call.args,
                        reason=f"oracle agent: {payload.message[:200]}",
                    )
                except ForgeError as exc:
                    result = AgentToolResult(
                        tool=call.tool,
                        args=call.args,
                        decision="blocked",
                        ok=False,
                        error=str(exc),
                    )
                else:
                    result = AgentToolResult(
                        tool=str(data.get("tool") or call.tool),
                        args=call.args,
                        decision=str(data.get("decision") or "blocked"),
                        ok=data.get("ok"),
                        output=data.get("output") or "",
                        error=data.get("error"),
                        approval_id=data.get("approval_id"),
                    )
            results.append(result)
            messages.append(
                {
                    "role": "user",
                    "content": _format_tool_result(result, settings.agent_tool_output_limit),
                }
            )

        return JSONResponse(
            {
                "reply": reply,
                "steps": len(results),
                "tools": [result.model_dump() for result in results],
                "model": metadata["model"],
                "tokens": metadata["tokens"],
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
        )

    @application.post(
        "/v1/decide", response_model=DecideResponse, dependencies=[Depends(_require_auth)]
    )
    async def decide(payload: DecideRequest, request: Request) -> JSONResponse:
        """Ask the model for a structured watchdog decision.

        The situation snapshot is sent as the only context and the model must
        answer with the strict JSON contract defined in ``decision_prompt``.
        Deliberately stateless: watchdog decisions never use conversation
        history.
        """
        instance: OllamaClient = request.app.state.ollama
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": settings.decision_prompt},
            {"role": "user", "content": payload.situation},
        ]
        try:
            content, metadata = await instance.chat(
                messages, temperature=settings.decision_temperature
            )
        except OllamaError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return JSONResponse(
            {
                "reply": content,
                "model": metadata["model"],
                "tokens": metadata["tokens"],
                "latency_ms": metadata["latency_ms"],
            }
        )

    return application


def _parse_tool_call(content: str) -> AgentToolCall | None:
    """Read a strict JSON tool call from the model reply, or None for a plain
    answer. Tolerates ``` code fences around the JSON object."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    tool = data.get("tool")
    if not isinstance(tool, str) or not tool:
        return None
    args = data.get("args")
    if not isinstance(args, dict):
        args = {}
    return AgentToolCall(tool=tool, args=args)


def _message_size(messages: list[dict[str, Any]]) -> int:
    """Total characters across every message (a cheap proxy for tokens)."""
    return sum(len(str(m.get("content") or "")) for m in messages)


def _over_budget(messages: list[dict[str, Any]], budget: int) -> bool:
    return bool(budget) and _message_size(messages) > budget


def _format_tool_result(result: AgentToolResult, limit: int) -> str:
    """Compact, model-readable rendering of a tool execution outcome, with the
    output truncated to ``limit`` characters (0 = unlimited) so long results
    cannot blow up the context window."""
    head = f"Tool {result.tool} result (decision={result.decision}"
    if result.approval_id:
        head += f", approval_id={result.approval_id}"
    if result.error:
        head += f", error: {result.error}"
    head += "):"
    body = result.output if result.output else "no output"
    if limit and len(body) > limit:
        body = f"{body[:limit]}\n... [truncated {len(body) - limit} more chars]"
    return f"{head}\n{body}"


app = create_app()
