from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import APIRouter, FastAPI

from payment_service.api.errors import register_error_handlers
from payment_service.api.routers import payments
from payment_service.api.security import ApiKeyGuard
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
    )
    app.state.settings = settings

    register_error_handlers(app)

    api = APIRouter(prefix="/api/v1", dependencies=[ApiKeyGuard])
    api.include_router(payments.router)
    app.include_router(api)

    @app.get("/health", tags=["service"], summary="Проверка состояния сервиса")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
