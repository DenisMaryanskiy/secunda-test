from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from payment_service.config import Settings
from payment_service.db import SessionFactory
from payment_service.services.payments import PaymentService


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    # Фабрика собирается в lifespan и живёт в state приложения, а не в глобальной переменной.
    factory: SessionFactory = request.app.state.session_factory
    async with factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_payment_service(session: SessionDep) -> PaymentService:
    return PaymentService(session)


PaymentServiceDep = Annotated[PaymentService, Depends(get_payment_service)]
