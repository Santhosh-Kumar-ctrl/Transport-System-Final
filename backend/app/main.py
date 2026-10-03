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

from app import db_models  # noqa: F401  (registers all tables)
from app.core import events, tasks
from app.core.config import settings
from app.core.errors import DomainError
from app.core.realtime import hub, router as realtime_router
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
    tracking,
    trips,
)

MODULES = [
    auth, master_data, trips, tracking, allocation, boarding,
    capacity, delay_monitor, notifications, history, dashboard,
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.enable_background_tasks:
        tasks.start_all()
    yield
    await tasks.stop_all()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Transit: College Transport Platform",
        version="0.1.0",
        description="P0 operations API. Modules: " + ", ".join(m.__name__.rsplit(".", 1)[-1] for m in MODULES),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
        allow_methods=["*"], allow_headers=["*"],
    )

    @app.exception_handler(DomainError)
    async def domain_error_handler(_: Request, exc: DomainError):
        headers = {"Retry-After": str(exc.extra["retry_after"])} if "retry_after" in exc.extra else None
        return JSONResponse(status_code=exc.status_code, headers=headers,
                            content={"detail": exc.message, "code": exc.code, **exc.extra})

    events.clear_subscribers()  # create_app() may run more than once (tests)
    for module in MODULES:
        app.include_router(module.router)
        if hasattr(module, "register"):
            module.register(app)
    app.include_router(realtime_router)

    @app.get("/health", tags=["meta"])
    async def health():
        return {"status": "ok", "ws_connections": hub.connection_count()}

    return app


app = create_app()
