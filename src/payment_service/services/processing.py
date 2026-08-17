import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol

import structlog

from payment_service.adapters.gateway import ChargeResult, PaymentGateway
from payment_service.adapters.webhooks import WebhookNotifier
from payment_service.enums import PaymentStatus
from payment_service.models import Payment

log = structlog.get_logger(__name__)


class PaymentStore(Protocol):
    async def get(self, payment_id: uuid.UUID) -> Payment | None: ...

    async def finish_processing(
        self,
        payment_id: uuid.UUID,
        *,
        status: PaymentStatus,
        failure_reason: str | None,
    ) -> Payment | None: ...

    async def mark_webhook_delivered(self, payment_id: uuid.UUID) -> None: ...


# Каждый вызов открывает свою короткую транзакцию и закрывает её на выходе.
type PaymentsTransaction = Callable[[], AbstractAsyncContextManager[PaymentStore]]


class PaymentProcessor:
    """
    Сценарий обработки платежа: сходить в шлюз, зафиксировать результат, уведомить клиента.

    Доставка событий at-least-once, поэтому сценарий рассчитан на повторный вызов:
    поход в шлюз пропускается для уже обработанного платежа, webhook - для уже
    доставленного. Оба шага идемпотентны по отдельности, потому что упасть можно
    между ними.

    Транзакция открывается на каждое обращение к БД отдельно. Одну на весь сценарий
    держать нельзя: поход в шлюз занимает секунды, и всё это время соединение
    висело бы в состоянии idle in transaction.
    """

    def __init__(
        self,
        payments: PaymentsTransaction,
        gateway: PaymentGateway,
        notifier: WebhookNotifier,
    ) -> None:
        self._payments = payments
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
