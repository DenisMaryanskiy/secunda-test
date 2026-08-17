from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from payment_service.db import SessionFactory


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    # Фабрика собирается в lifespan и живёт в state приложения, а не в глобальной переменной.
    factory: SessionFactory = request.app.state.session_factory
    async with factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
