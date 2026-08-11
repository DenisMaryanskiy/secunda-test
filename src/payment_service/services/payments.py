import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from payment_service.errors import IdempotencyConflictError, PaymentNotFoundError
from payment_service.messaging.events import payment_created_message
from payment_service.models import Payment
from payment_service.repositories.outbox import OutboxRepository
from payment_service.repositories.payments import PaymentRepository
from payment_service.services.commands import NewPayment
from payment_service.services.idempotency import request_fingerprint

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CreatedPayment:
    payment: Payment
    # Повтор по тому же ключу идемпотентности: платёж не создавался заново.
    replayed: bool


class PaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._payments = PaymentRepository(session)
        self._outbox = OutboxRepository(session)

    async def get(self, payment_id: uuid.UUID) -> Payment:
        payment = await self._payments.get(payment_id)
        if payment is None:
            raise PaymentNotFoundError(payment_id)
        return payment

    async def create(self, new_payment: NewPayment, idempotency_key: str) -> CreatedPayment:
        fingerprint = request_fingerprint(new_payment)

        replay = await self._find_replay(idempotency_key, fingerprint)
        if replay is not None:
            return CreatedPayment(payment=replay, replayed=True)

        payment = Payment(
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            amount=new_payment.amount,
            currency=new_payment.currency,
            description=new_payment.description,
            meta=new_payment.metadata,
            webhook_url=new_payment.webhook_url,
        )

        try:
            self._payments.add(payment)
            # flush проставляет created_at, без которого не собрать событие.
            await self._session.flush()
            self._outbox.add(payment_created_message(payment))
            # Платёж и событие уезжают одной транзакцией.
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            # Гонка: параллельный запрос с тем же ключом успел вставить строку раньше.
            replay = await self._find_replay(idempotency_key, fingerprint)
            if replay is None:
                raise
            return CreatedPayment(payment=replay, replayed=True)

        log.info("payment.created", payment_id=str(payment.id), amount=str(payment.amount))
        return CreatedPayment(payment=payment, replayed=False)

    async def _find_replay(self, idempotency_key: str, fingerprint: str) -> Payment | None:
        existing = await self._payments.get_by_idempotency_key(idempotency_key)
        if existing is None:
            return None
        if existing.request_fingerprint != fingerprint:
            raise IdempotencyConflictError(idempotency_key)
        return existing
