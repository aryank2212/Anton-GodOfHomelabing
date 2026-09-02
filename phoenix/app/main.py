"""Application factory for Phoenix.

The lifespan wires the full stack:

* YAML infrastructure configuration (monitors, components, dependencies),
* environment / process settings (``PHOENIX_`` variables),
* the incident database,
* monitors, recovery strategies, the dependency graph,
* the Hermes publisher,
* the continuous monitoring scheduler.

``settings`` is injected so tests can point Phoenix at a throwaway database
and configuration. When omitted, configuration is read from the environment.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.middleware import RequestIDMiddleware
from app.api.routes import (
    health_router,
    incidents_router,
    maintenance_router,
    recovery_router,
    statistics_router,
)
from app.config.loader import load_config
from app.config.models import PhoenixConfig
from app.config.settings import Settings
from app.core.clients import Clients
from app.core.logging import get_logger, setup_logging
from app.core.scheduler import MonitorScheduler
from app.database.repository import Repository
from app.database.session import Database
from app.monitors.registry import default_registry
from app.recovery.registry import default_registry as default_recovery_registry
from app.services.dependency_graph import DependencyGraph
from app.services.hermes import HermesPublisher
from app.services.incidents import IncidentService
from app.services.maintenance import MaintenanceService
from app.services.orchestrator import Orchestrator
from app.services.snapshot import HealthSnapshot
from app.services.statistics import StatisticsService

log = get_logger(__name__)


def _build_monitors(config: PhoenixConfig, clients: Clients):
    registry = default_registry()
    monitors = [registry.build(spec, clients) for spec in config.monitors if spec.enabled]
    return registry, monitors


def _scheduler_settings(settings: Settings, config: PhoenixConfig) -> tuple[float, int]:
    tick = (
        config.scheduler.tick_interval
        if config.scheduler.tick_interval is not None
        else settings.scheduler_tick_interval
    )
    concurrency = (
        config.scheduler.max_concurrent_checks
        if config.scheduler.max_concurrent_checks is not None
        else settings.scheduler_max_concurrent_checks
    )
    return tick, concurrency


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = load_config(settings.config_file)
        database = Database(settings.database_url)
        await database.init()

        repository = Repository(database.session_factory)
        incidents = IncidentService(repository)
        maintenance = MaintenanceService(repository)
        statistics = StatisticsService(repository)

        clients = Clients.defaults()
        monitor_registry, monitors = _build_monitors(config, clients)
        recovery_registry = default_recovery_registry()
        graph = DependencyGraph(config)
        graph.validate()

        publisher = HermesPublisher(settings)
        snapshot = HealthSnapshot()
        orchestrator = Orchestrator(
            config=config,
            clients=clients,
            incidents=incidents,
            maintenance=maintenance,
            publisher=publisher,
            recovery_registry=recovery_registry,
            monitor_registry=monitor_registry,
            graph=graph,
        )

        tick, concurrency = _scheduler_settings(settings, config)
        scheduler = MonitorScheduler(
            config,
            monitors,
            orchestrator,
            snapshot,
            tick_interval=tick,
            max_concurrent=concurrency,
        )
        if settings.scheduler_enabled:
            await scheduler.start()

        app.state.settings = settings
        app.state.config = config
        app.state.database = database
        app.state.repository = repository
        app.state.incidents = incidents
        app.state.maintenance = maintenance
        app.state.statistics = statistics
        app.state.orchestrator = orchestrator
        app.state.publisher = publisher
        app.state.snapshot = snapshot
        app.state.scheduler = scheduler
        app.state.clients = clients

        log.info(
            "phoenix_started",
            extra={
                "version": settings.version,
                "environment": settings.environment,
                "monitors": len(monitors),
                "components": len(config.components),
                "hermes": settings.hermes_event_url if settings.hermes_enabled else "disabled",
            },
        )
        try:
            yield
        finally:
            await scheduler.stop()
            await clients.aclose()
            await publisher.aclose()
            await database.dispose()
            log.info("phoenix_stopped")

    app = FastAPI(
        title="Anton Phoenix",
        description=(
            "Phoenix is Anton's autonomous recovery and self-healing subsystem. "
            "It observes Anton, diagnoses failures, attempts recovery, records "
            "incidents and publishes standardized events to Hermes. It never "
            "notifies users directly and never runs AI workloads."
        ),
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(RequestIDMiddleware)
    app.include_router(health_router)
    app.include_router(incidents_router)
    app.include_router(statistics_router)
    app.include_router(recovery_router)
    app.include_router(maintenance_router)
    return app


app = create_app()
