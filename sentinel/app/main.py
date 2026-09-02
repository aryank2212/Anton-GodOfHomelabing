"""Application factory for Sentinel.

The lifespan wires the full perception stack:

* environment / process settings (``SENTINEL_`` variables),
* YAML configuration (observers, rules, devices, vendors),
* the observation database,
* observers, the device tracker, the correlation and presence engines,
* the Hermes publisher,
* the observer scheduler and engine tickers.

``settings`` is injected so tests can point Sentinel at a throwaway database.
When omitted, configuration is read from the environment.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.middleware import RequestIDMiddleware
from app.api.routes import (
    devices_router,
    health_router,
    observations_router,
    observers_router,
    presence_router,
    situations_router,
)
from app.config.settings import Settings
from app.core.logging import get_logger, setup_logging
from app.core.runtime import SentinelRuntime

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime = SentinelRuntime(settings)
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
        title="Anton Sentinel",
        description=(
            "Sentinel is Anton's perception and situational awareness subsystem. "
            "It observes Anton from many independent sources, correlates the "
            "observations into situations, tracks presence and device inventory, "
            "and publishes standardized events to Hermes. It never recovers, "
            "never notifies and never runs AI workloads."
        ),
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(RequestIDMiddleware)
    app.include_router(health_router)
    app.include_router(observations_router)
    app.include_router(situations_router)
    app.include_router(presence_router)
    app.include_router(devices_router)
    app.include_router(observers_router)
    return app


app = create_app()
