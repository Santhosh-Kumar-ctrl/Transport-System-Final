"""App composition root: the ONLY place that knows the full module list.

Each module exposes `router` and optionally `register(app)` (event subscriptions,
background jobs). Order matters only for readability; modules talk through
services and events, not through each other's routers.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import db_models  # noqa: F401  (registers all tables)
from app.core import events, realtime, tasks
from app.core.config import settings
from app.core.db import engine
from app.core.errors import DomainError
from app.modules import (
    allocation,
    auth,
    boarding,
    capacity,
    dashboard,
    delay_monitor,
    history,
    master_data,
    notifications,
    reports,
    tracking,
    trips,
)

MODULES = [
    auth, master_data, trips, tracking, allocation, boarding,
    capacity, delay_monitor, notifications, history, dashboard, reports,
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.enable_background_tasks:
        tasks.start_all()
    yield
    await tasks.stop_all()


def create_app() -> FastAPI:
    if settings.is_production and (problems := settings.production_problems()):
        raise RuntimeError("Refusing to start in production:\n  - " + "\n  - ".join(problems))
    docs = {} if not settings.is_production else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    app = FastAPI(
        title="Transit: College Transport Platform",
        version="0.1.0",
        description="P0 operations API. Modules: " + ", ".join(m.__name__.rsplit(".", 1)[-1] for m in MODULES),
        lifespan=lifespan,
        **docs,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
        allow_methods=["*"], allow_headers=["*"],
    )

    @app.exception_handler(DomainError)
    async def domain_error_handler(_: Request, exc: DomainError):
        headers = {"Retry-After": str(exc.extra["retry_after_seconds"])} if "retry_after_seconds" in exc.extra else None
        return JSONResponse(status_code=exc.status_code,
                            content={"detail": exc.message, "code": exc.code, **exc.extra}, headers=headers)

    events.clear_subscribers()  # create_app() may run more than once (tests)
    realtime.clear_policies()
    for module in MODULES:
        app.include_router(module.router)
        if hasattr(module, "register"):
            module.register(app)
    app.include_router(realtime.router)

    @app.get("/health", tags=["meta"])
    async def health():
        """Liveness + database check, for the reverse proxy and monitoring."""
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=503, content={"status": "db_unavailable"})
        return {"status": "ok", "ws_connections": realtime.hub.connection_count()}

    return app


app = create_app()
