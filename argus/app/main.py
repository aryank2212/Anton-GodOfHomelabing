"""Application factory for Argus.

The lifespan wires the full intelligence stack:

* environment / process settings (``ARGUS_`` variables),
* YAML configuration (feeds, sites, providers, channels),
* the evidence database,
* collectors, entity extraction/resolution/correlation, change detection,
* the hypothesis engine and the report generator,
* the Hermes publisher and the Oracle gateway.

``settings`` is injected so tests can point Argus at a throwaway database.
When omitted, configuration is read from the environment.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.middleware import RequestIDMiddleware
from app.api.routes import api_router
from app.api.routes.health import router as health_router
from app.config.settings import Settings, get_settings
from app.core.logging import get_logger, setup_logging
from app.core.runtime import ArgusRuntime

log = get_logger(__name__)

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Re-assert our handler/level here: uvicorn reconfigures root logging
        # after the app module imports, which can silently replace the JSON
        # handler configured below. Running it again inside the lifespan
        # guarantees runtime logs reach the container in structured form.
        setup_logging(settings.log_level)
        runtime = ArgusRuntime(settings)
        await runtime.start()
        app.state.runtime = runtime
        log.info(
            "api_ready",
            extra={"port": settings.api_port, "environment": settings.environment},
        )
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(
        title="Anton Argus",
        description=(
            "Argus is Anton's internet-facing ingestion and intelligence "
            "subsystem. It collects content from RSS/news feeds, tracked "
            "websites, OSINT APIs and Telegram channels, extracts and "
            "correlates entities, detects changes, generates hypotheses and "
            "publishes intelligence reports to Hermes. It never acts and "
            "never notifies anyone directly."
        ),
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(RequestIDMiddleware)
    app.include_router(api_router)
    # Docker healthchecks and uptime monitors probe /health (stack convention,
    # same as Sentinel/Hermes); the full API also exposes /v1/health.
    app.include_router(health_router)

    # Serve the built command-center SPA when present (web/index.html + assets).
    # In tests or a repo without a web build, the API routes remain the whole app.
    web_index = _WEB_DIR / "index.html"
    if web_index.exists():
        assets_dir = _WEB_DIR / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="web-assets")
        app.mount(
            "/static", StaticFiles(directory=_WEB_DIR / "static"), name="web-static"
        ) if (_WEB_DIR / "static").is_dir() else None

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(web_index)

        @app.get("/{path:path}", include_in_schema=False)
        async def web_fallback(path: str) -> FileResponse:
            # Never shadow API/docs routes (already matched earlier); anything
            # else is a client side route for the SPA.
            candidate = (_WEB_DIR / path).resolve()
            try:
                candidate.relative_to(_WEB_DIR.resolve())
            except ValueError:
                return FileResponse(web_index)
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(web_index)
    else:
        @app.get("/", include_in_schema=False)
        async def root() -> dict[str, str]:
            return {"name": "argus", "docs": "/docs", "health": "/v1/health"}

    return app


app = create_app()
