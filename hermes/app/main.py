from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.middleware import RequestIDMiddleware
from app.api.routes import approvals_router, events_router, health_router
from app.bot.telegram_bot import TelegramBot
from app.config.settings import Settings
from app.core.logging import get_logger, setup_logging
from app.core.queue import NotificationQueue
from app.core.renderer import Renderer
from app.database.session import Database
from app.providers.registry import ProviderRegistry
from app.rules.engine import RuleEngine
from app.rules.loader import load_rules
from app.services.approvals import ApprovalBridge
from app.services.dispatcher import Dispatcher
from app.services.oracle import OracleClient
from app.services.remediator import Remediator
from app.services.storm import StormDetector
from app.services.watchdog import Watchdog

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory.

    ``settings`` is provided so tests can point Hermes at a throwaway
    database and rule file. When omitted, configuration is read from the
    environment / .env file.
    """
    settings = settings or Settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = Database(settings.database_url)
        await database.init()

        engine = RuleEngine(load_rules(settings.rules_file))
        registry = ProviderRegistry(settings)
        renderer = Renderer(settings.templates_dir)
        remediator = Remediator(settings=settings, renderer=renderer)
        dispatcher = Dispatcher(
            settings=settings,
            database=database,
            registry=registry,
            engine=engine,
            renderer=renderer,
            remediator=remediator,
        )
        queue = NotificationQueue(
            dispatcher.process,
            session_factory=database.session_factory,
            concurrency=settings.worker_concurrency,
            sweep_interval=settings.worker_sweep_interval,
        )
        await queue.start()

        storm = StormDetector(settings=settings, database=database, queue=queue)
        oracle = OracleClient(settings, database)
        bot = TelegramBot(
            settings=settings,
            database=database,
            queue=queue,
            registry=registry,
            oracle=oracle,
        )
        watchdog = Watchdog(
            settings=settings,
            database=database,
            queue=queue,
            registry=registry,
            oracle=oracle,
            bot=bot,
        )
        approvals = ApprovalBridge(settings=settings, database=database, queue=queue, bot=bot)
        bot.attach_watchdog(watchdog)
        bot.attach_approvals(approvals)
        await storm.start()
        await bot.start()
        await watchdog.start()
        await approvals.start()

        app.state.settings = settings
        app.state.database = database
        app.state.queue = queue
        app.state.registry = registry
        app.state.dispatcher = dispatcher
        app.state.storm = storm
        app.state.bot = bot
        app.state.oracle = oracle
        app.state.watchdog = watchdog
        app.state.approvals = approvals

        log.info(
            "hermes_started",
            extra={
                "version": settings.version,
                "environment": settings.environment,
                "providers": registry.names,
                "rules": engine.rule_count,
                "storm": storm.enabled,
                "telegram_bot": bot.enabled,
                "ai": oracle.enabled,
                "watchdog": watchdog.enabled,
                "approvals": approvals.enabled,
            },
        )
        try:
            yield
        finally:
            await watchdog.stop()
            await bot.stop()
            await approvals.stop()
            await storm.stop()
            await queue.stop()
            await dispatcher.close()
            await registry.close()
            await database.dispose()
            log.info("hermes_stopped")

    app = FastAPI(
        title="Anton Hermes",
        description=(
            "Hermes is the communication and event system for Anton. "
            "It is the only service allowed to talk to notification providers."
        ),
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(RequestIDMiddleware)
    app.include_router(health_router)
    app.include_router(events_router)
    app.include_router(approvals_router)
    return app


app = create_app()
