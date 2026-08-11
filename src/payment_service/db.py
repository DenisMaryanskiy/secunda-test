from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from payment_service.config import DatabaseSettings

__all__ = ["SessionFactory", "create_engine", "create_session_factory"]

type SessionFactory = async_sessionmaker[AsyncSession]


def create_engine(settings: DatabaseSettings) -> AsyncEngine:
    return create_async_engine(
        str(settings.url),
        echo=settings.echo,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    # expire_on_commit=False: после коммита объект остаётся пригодным для чтения,
    # иначе сервис не сможет отдать созданный платёж наружу без повторного запроса.
    return async_sessionmaker(engine, expire_on_commit=False)
