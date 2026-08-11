import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog

from payment_service.db import SessionFactory
from payment_service.enums import PaymentStatus
from payment_service.models import Payment
from payment_service.repositories.payments import PaymentRepository
from payment_service.services.gateway import ChargeResult, PaymentGateway
from payment_service.services.webhooks import WebhookNotifier

log = structlog.get_logger(__name__)


class PaymentProcessor:
    """
    Сценарий обработки платежа: сходить в шлюз, зафиксировать результат, уведомить клиента.

    Доставка событий at-least-once, поэтому сценарий рассчитан на повторный вызов:
    поход в шлюз пропускается для уже обработанного платежа, webhook - для уже
    доставленного. Оба шага идемпотентны по отдельности, потому что упасть можно
    между ними.

    Каждое обращение к БД - отдельная короткая транзакция. Одну открытую на весь
    сценарий держать нельзя: поход в шлюз занимает секунды, и всё это время
    соединение висело бы в состоянии idle in transaction.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        gateway: PaymentGateway,
        notifier: WebhookNotifier,
    ) -> None:
        self._session_factory = session_factory
        self._gateway = gateway
        self._notifier = notifier

    async def process(self, payment_id: uuid.UUID) -> None:
        payment = await self._load(payment_id)
        if payment is None:
            # Платежа нет - повторять бессмысленно, сообщение подтверждаем.
            log.warning("payment.missing", payment_id=str(payment_id))
            return

        if payment.status is PaymentStatus.PENDING:
            result = await self._gateway.charge(payment_id)
            payment = await self._finish(payment_id, result)
            if payment is None:
                log.info("payment.already_processed", payment_id=str(payment_id))
                return
            log.info("payment.processed", payment_id=str(payment_id), status=payment.status.value)

        if payment.webhook_delivered_at is not None:
            log.info("webhook.already_delivered", payment_id=str(payment_id))
            return

        await self._notifier.notify(payment)
        await self._mark_webhook_delivered(payment_id)

    @asynccontextmanager
    async def _payments(self) -> AsyncIterator[PaymentRepository]:
        """Репозиторий в своей короткой транзакции: он привязан к сессии, общей быть не может."""
        async with self._session_factory() as session:
            yield PaymentRepository(session)
            await session.commit()

    async def _load(self, payment_id: uuid.UUID) -> Payment | None:
        async with self._payments() as payments:
            return await payments.get(payment_id)

    async def _finish(self, payment_id: uuid.UUID, result: ChargeResult) -> Payment | None:
        status = PaymentStatus.SUCCEEDED if result.succeeded else PaymentStatus.FAILED
        async with self._payments() as payments:
            return await payments.finish_processing(
                payment_id, status=status, failure_reason=result.failure_reason
            )

    async def _mark_webhook_delivered(self, payment_id: uuid.UUID) -> None:
        async with self._payments() as payments:
            await payments.mark_webhook_delivered(payment_id)
