"""Forge HTTP API.

Routes (all but /health require the bearer token in ``FORGE_TOKEN``):

    GET  /health                     liveness + policy summary
    GET  /v1/tools                   advertise tools to the Oracle agent
    POST /v1/run                     execute a tool call (staged autonomy)
    GET  /v1/approvals               list pending approvals
    POST /v1/approvals/{id}/resolve  operator verdict (from Hermes)
    GET  /v1/runs                    recent audit trail
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status

from app import __version__
from app.approval import ApprovalStore
from app.audit import AuditLog
from app.config import Settings, get_settings
from app.engine import ExecutionEngine, HermesClient
from app.policy import Policy, load_policy_config
from app.schemas import ApprovalOut, ApprovalResolve, RunOut, ToolCall, ToolCallResponse
from app.state import StateManager
from app.tools.registry import RegistryFactory, ToolRegistry

log = logging.getLogger("forge")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        policy_config = load_policy_config(settings.config_file)
        state = StateManager(settings.state_file)
        audit = AuditLog(settings.audit_file)
        approvals = ApprovalStore(settings.approval_timeout)
        hermes = HermesClient(settings.hermes_url, settings.http_timeout)

        factory = RegistryFactory(
            settings, policy=Policy.from_config(policy_config, set()), state=state
        )
        registry = factory.build()
        policy = Policy.from_config(policy_config, registry.read_only_names())
        engine = ExecutionEngine(
            registry=registry,
            policy=policy,
            approvals=approvals,
            audit=audit,
            settings=settings,
            hermes=hermes,
        )

        async def sweeper() -> None:
            while True:
                try:
                    await engine.sweep()
                except Exception:  # noqa: BLE001
                    log.exception("forge_sweep_failed")
                await asyncio.sleep(settings.approval_sweep_interval)

        sweep_task = asyncio.create_task(sweeper(), name="forge-sweeper")

        application.state.settings = settings
        application.state.registry = registry
        application.state.policy = policy
        application.state.engine = engine
        application.state.audit = audit
        application.state.approvals = approvals
        application.state.state = state

        log.info(
            "forge_started",
            extra={
                "version": settings.version,
                "environment": settings.environment,
                "autonomy_level": policy.autonomy_level,
                "tools": registry.names,
                "managed_repos": [r.path for r in policy.config.managed_repos],
                "projects": [p.name for p in policy.config.compose_projects],
            },
        )
        try:
            yield
        finally:
            sweep_task.cancel()
            await engine.close()
            await factory.close()

    application = FastAPI(
        title="Anton Forge",
        description=(
            "Forge is the execution layer for Anton: a tool API with staged "
            "autonomy (diagnose / approve / auto) over Docker, Phoenix and git."
        ),
        version=__version__,
        lifespan=lifespan,
    )

    async def require_token(authorization: str | None = Header(default=None)) -> None:
        if not settings.auth_enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Forge is not configured: set FORGE_TOKEN",
            )
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
        provided = authorization[len("Bearer ") :].strip()
        if not secrets.compare_digest(provided, settings.token or ""):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

    def _caller(request: Request) -> str:
        return request.headers.get("x-forge-caller", "unknown")[:64]

    @application.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        policy: Policy = request.app.state.policy
        registry: ToolRegistry = request.app.state.registry
        approvals = await request.app.state.approvals.pending()
        return {
            "status": "ok",
            "version": __version__,
            "environment": settings.environment,
            "autonomy_level": policy.autonomy_level,
            "tools": len(registry.names),
            "pending_approvals": len(approvals),
            "auth_enabled": settings.auth_enabled,
        }

    @application.get("/v1/tools", dependencies=[Depends(require_token)])
    async def tools(request: Request) -> dict[str, Any]:
        registry: ToolRegistry = request.app.state.registry
        return {"tools": registry.specs()}

    @application.post(
        "/v1/run", response_model=ToolCallResponse, dependencies=[Depends(require_token)]
    )
    async def run(payload: ToolCall, request: Request) -> ToolCallResponse:
        engine: ExecutionEngine = request.app.state.engine
        return await engine.call(payload, caller=_caller(request))

    @application.get(
        "/v1/approvals", response_model=list[ApprovalOut], dependencies=[Depends(require_token)]
    )
    async def approvals(request: Request) -> list[ApprovalOut]:
        approvals = await request.app.state.approvals.pending()
        return [a.to_out() for a in approvals]

    @application.post(
        "/v1/approvals/{approval_id}/resolve",
        response_model=ToolCallResponse | None,
        dependencies=[Depends(require_token)],
    )
    async def resolve(approval_id: str, payload: ApprovalResolve, request: Request) -> Any:
        engine: ExecutionEngine = request.app.state.engine
        response = await engine.resolve(approval_id, payload.approved, by=payload.by)
        if response is None:
            approval = await request.app.state.approvals.get(approval_id)
            if approval is None:
                raise HTTPException(status_code=404, detail="approval not found")
            return None
        return response

    @application.get("/v1/runs", response_model=list[RunOut], dependencies=[Depends(require_token)])
    async def runs(request: Request, limit: int = 50) -> list[RunOut]:
        audit: AuditLog = request.app.state.audit
        rows = await audit.recent(limit=min(max(limit, 1), 200))
        return [RunOut.model_validate(row) for row in rows]

    return application


app = create_app()
