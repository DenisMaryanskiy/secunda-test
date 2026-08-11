from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from payment_service.models import OutboxMessage
from payment_service.models.base import utcnow

MAX_ERROR_LENGTH = 1000


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, message: OutboxMessage) -> None:
        self._session.add(message)

    async def fetch_unpublished(self, limit: int) -> Sequence[OutboxMessage]:
        """
        Забирает пачку неопубликованных событий и держит их заблокированными
        до конца транзакции.

        SKIP LOCKED позволяет запустить relay в несколько процессов: соседний
        не встаёт в очередь за уже занятыми записями, а берёт следующие свободные.
        """
        statement = (
            select(OutboxMessage)
            .where(OutboxMessage.published_at.is_(None))
            .order_by(OutboxMessage.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.scalars(statement)
        return result.all()

    def mark_published(self, message: OutboxMessage) -> None:
        message.published_at = utcnow()

    def record_failure(self, message: OutboxMessage, error: str) -> None:
        message.attempts += 1
        message.last_error = error[:MAX_ERROR_LENGTH]
