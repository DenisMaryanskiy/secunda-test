import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from payment_service.enums import PaymentStatus
from payment_service.models import Payment
from payment_service.models.base import utcnow


class PaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, payment: Payment) -> None:
        self._session.add(payment)

    async def get(self, payment_id: uuid.UUID) -> Payment | None:
        return await self._session.get(Payment, payment_id)

    async def finish_processing(
        self,
        payment_id: uuid.UUID,
        *,
        status: PaymentStatus,
        failure_reason: str | None,
    ) -> Payment | None:
        """
        Переводит платёж из pending в терминальный статус.

        Условие по текущему статусу защищает от параллельной обработки того же
        сообщения: вернёт None, если платёж успел завершить кто-то другой.
        """
        statement = (
            update(Payment)
            .where(Payment.id == payment_id, Payment.status == PaymentStatus.PENDING)
            .values(status=status, failure_reason=failure_reason, processed_at=utcnow())
            .returning(Payment)
        )
        updated = await self._session.scalars(statement)
        return updated.one_or_none()

    async def mark_webhook_delivered(self, payment_id: uuid.UUID) -> None:
        statement = (
            update(Payment).where(Payment.id == payment_id).values(webhook_delivered_at=utcnow())
        )
        await self._session.execute(statement)

    async def get_by_idempotency_key(self, idempotency_key: str) -> Payment | None:
        statement = select(Payment).where(Payment.idempotency_key == idempotency_key)
        result = await self._session.scalars(statement)
        return result.one_or_none()
