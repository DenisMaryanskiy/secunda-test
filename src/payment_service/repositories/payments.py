import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from payment_service.models import Payment


class PaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, payment: Payment) -> None:
        self._session.add(payment)

    async def get(self, payment_id: uuid.UUID) -> Payment | None:
        return await self._session.get(Payment, payment_id)

    async def get_by_idempotency_key(self, idempotency_key: str) -> Payment | None:
        statement = select(Payment).where(Payment.idempotency_key == idempotency_key)
        result = await self._session.scalars(statement)
        return result.one_or_none()
