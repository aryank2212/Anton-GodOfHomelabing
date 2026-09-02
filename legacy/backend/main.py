import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from .database import engine, SessionLocal
from . import models, auth
from .config import config
from .logging_config import setup_logging, get_logger
from .exceptions import register_exception_handlers
from .security import apply_security_headers, is_trusted_host, client_ip
from .services.queue_service import queue

from .routers import pages, entries, ai, memory, knowledge, events
from .routers import auth as auth_router
from .routers import health as health_router
from .routers import admin as admin_router
from .routers import export as export_router

setup_logging(config._data)

logger = get_logger("legacy.main")

models.Base.metadata.create_all(bind=engine)


def _ensure_user_columns():
    from sqlalchemy import inspect, text
    from .database import engine as _engine
    insp = inspect(_engine)
    columns = {c["name"] for c in insp.get_columns("users")}
    dialect = _engine.dialect.name
    additions = {
        "email_verified": (
            "ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0"
            if dialect == "sqlite"
            else "ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT FALSE"
        ),
        "must_change_password": (
            "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0"
            if dialect == "sqlite"
            else "ALTER TABLE users ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT FALSE"
        ),
    }
    for name, stmt in additions.items():
        if name in columns:
            continue
        with _engine.connect() as conn:
            conn.execute(text(stmt))
            conn.commit()
        logger.info(f"Migrated users table: added {name} column")


def _enforce_default_admin_password_policy(db):
    if not config.security.get("force_password_change_on_default", True):
        return
    admin = db.query(models.User).filter(models.User.role == "admin").first()
    if admin and not auth.user_must_change_password(admin):
        if auth._verify_password("admin", admin.password_hash):
            admin.must_change_password = 1
            db.commit()
            logger.warning(
                "Admin account still uses the default password - forcing password change"
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"LEGACY v{config.app.get('version', '3.0.0')} starting...")
    _ensure_user_columns()
    queue.start()

    db = SessionLocal()
    try:
        from .auth import AUTH_ENABLED
        if AUTH_ENABLED:
            admin_exists = db.query(models.User).filter(
                models.User.role == "admin"
            ).first()
            if not admin_exists:
                from .auth import create_user
                create_user(
                    db, "admin", "admin",
                    email="admin@legacy.local",
                    display_name="Administrator",
                    role="admin",
                )
                logger.info("Default admin user created (admin:admin) - CHANGE IMMEDIATELY")
            _enforce_default_admin_password_policy(db)
    except Exception as e:
        logger.error(f"Startup initialization error: {e}")
    finally:
        db.close()

    logger.info("LEGACY startup complete")
    yield
    logger.info("LEGACY shutting down...")
    queue.stop()
    logger.info("LEGACY shutdown complete")


app = FastAPI(
    title=config.app.get("name", "LEGACY"),
    description="Your permanent memory system. A production-grade memory platform for the LEGACY ecosystem.",
    version=config.app.get("version", "3.0.0"),
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    contact={
        "name": "LEGACY",
        "url": "https://github.com/anomalyco/legacy",
    },
    license_info={
        "name": "MIT",
    },
    lifespan=lifespan,
)

cors_origins = config.app.get("cors_origins", [])
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_path = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "static"
)
os.makedirs(static_path, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_path), name="static")

register_exception_handlers(app)

app.include_router(pages.router)
app.include_router(entries.router)
app.include_router(ai.router)
app.include_router(memory.router)
app.include_router(knowledge.router)
app.include_router(events.router)
app.include_router(auth_router.router)
app.include_router(health_router.router)
app.include_router(admin_router.router)
app.include_router(export_router.router)


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    request_id = str(uuid.uuid4())[:8]
    request.state.request_id = request_id

    response = await call_next(request)

    process_time = time.time() - start_time
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time-MS"] = str(round(process_time * 1000, 2))
    return response


@app.middleware("http")
async def enforce_security_headers(request: Request, call_next):
    response = await call_next(request)
    apply_security_headers(response)
    return response


@app.middleware("http")
async def validate_host(request: Request, call_next):
    if request.url.path != "/health":
        host = request.headers.get("host", "")
        if not is_trusted_host(host):
            return JSONResponse(
                {"detail": "Invalid Host header"}, status_code=400
            )
    return await call_next(request)


@app.middleware("http")
async def enforce_password_change(request: Request, call_next):
    path = request.url.path
    if path.startswith(("/static", "/api", "/login", "/register", "/change-password", "/health")):
        return await call_next(request)

    db = SessionLocal()
    try:
        user = auth.get_current_user(request, db)
        if user and auth.user_must_change_password(user):
            return RedirectResponse(url="/change-password", status_code=302)
    finally:
        db.close()
    return await call_next(request)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=config.app.get("name", "LEGACY"),
        version=config.app.get("version", "3.0.0"),
        description="A production-grade memory platform for the LEGACY ecosystem. "
                    "Provides persistent memory, knowledge graphs, semantic search, "
                    "and agent integration capabilities.",
        routes=app.routes,
    )

    openapi_schema["x-logo"] = {
        "url": "/static/favicon.ico",
        "altText": "LEGACY",
    }

    openapi_schema["tags"] = [
        {"name": "Authentication", "description": "User login, logout, session management, and API key management"},
        {"name": "Entries", "description": "Journal entry CRUD operations"},
        {"name": "Memory", "description": "Memory creation, search, timeline, and reflections"},
        {"name": "AI", "description": "AI-powered graph, similarity search, entity management, and collections"},
        {"name": "Knowledge", "description": "Cross-source knowledge search, RAG context, and summaries"},
        {"name": "Events", "description": "System event publishing and querying"},
        {"name": "Admin", "description": "Administrative operations: users, audit logs, backup, queue management"},
        {"name": "Export/Import", "description": "Data export and import in multiple formats"},
        {"name": "Health", "description": "Health check, readiness, and liveness endpoints"},
    ]

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi
