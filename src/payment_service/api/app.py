from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from payment_service.api.errors import register_error_handlers
from payment_service.api.routers import api_router
from payment_service.api.routers import health as health_router
from payment_service.config import Settings, get_settings
from payment_service.db import create_engine, create_session_factory
from payment_service.logging import configure_logging

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    engine = create_engine(settings.database)
    app.state.session_factory = create_session_factory(engine)
    log.info("api.started", database=settings.database.safe_url)
    try:
        yield
    finally:
        await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="Payment Service",
        description="Асинхронная обработка платежей",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    app.state.settings = settings

    register_error_handlers(app)

    app.include_router(health_router.router)
    app.include_router(api_router)

    return app
