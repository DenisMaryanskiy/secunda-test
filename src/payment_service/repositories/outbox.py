from sqlalchemy.ext.asyncio import AsyncSession

from payment_service.models import OutboxMessage


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, message: OutboxMessage) -> None:
        self._session.add(message)
