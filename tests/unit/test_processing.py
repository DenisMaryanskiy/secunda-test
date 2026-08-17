import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from payment_service.adapters.gateway import ChargeResult
from payment_service.enums import PaymentStatus
from payment_service.models import Payment
from payment_service.models.base import utcnow
from payment_service.services.processing import PaymentProcessor, PaymentStore
from tests.factories import make_payment


class FakeGateway:
    def __init__(self, result: ChargeResult) -> None:
        self._result = result
        self.calls: list[uuid.UUID] = []

    async def charge(self, payment_id: uuid.UUID) -> ChargeResult:
        self.calls.append(payment_id)
        return self._result


class FakeNotifier:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[Payment] = []

    async def notify(self, payment: Payment) -> None:
        self.calls.append(payment)
        if self._error is not None:
            raise self._error


class FakeRepository:
    """Хранилище в памяти с той же семантикой, что у настоящего репозитория."""

    def __init__(self, payment: Payment | None, *, lost_race: bool = False) -> None:
        self.payment = payment
        self.lost_race = lost_race
        self.delivered_marks = 0

    async def get(self, payment_id: uuid.UUID) -> Payment | None:
        if self.payment is None or self.payment.id != payment_id:
            return None
        return self.payment

    async def finish_processing(
        self, payment_id: uuid.UUID, *, status: PaymentStatus, failure_reason: str | None
    ) -> Payment | None:
        if self.lost_race or self.payment is None:
            return None
        # Условие WHERE status = 'pending' из настоящего UPDATE.
        if self.payment.status is not PaymentStatus.PENDING:
            return None
        self.payment.status = status
        self.payment.failure_reason = failure_reason
        self.payment.processed_at = utcnow()
        return self.payment

    async def mark_webhook_delivered(self, payment_id: uuid.UUID) -> None:
        self.delivered_marks += 1
        if self.payment is not None:
            self.payment.webhook_delivered_at = utcnow()


def processor_for(
    repository: FakeRepository,
    gateway: FakeGateway,
    notifier: FakeNotifier,
) -> PaymentProcessor:
    @asynccontextmanager
    async def payments() -> AsyncIterator[PaymentStore]:
        yield repository

    return PaymentProcessor(payments, gateway, notifier)


async def test_successful_charge_notifies_client() -> None:
    payment = make_payment()
    repository = FakeRepository(payment)
    gateway = FakeGateway(ChargeResult(succeeded=True))
    notifier = FakeNotifier()

    await processor_for(repository, gateway, notifier).process(payment.id)

    assert payment.status is PaymentStatus.SUCCEEDED
    assert payment.failure_reason is None
    assert payment.processed_at is not None
    assert len(notifier.calls) == 1
    assert repository.delivered_marks == 1


async def test_failed_charge_records_reason_and_still_notifies() -> None:
    payment = make_payment()
    repository = FakeRepository(payment)
    gateway = FakeGateway(ChargeResult(succeeded=False, failure_reason="card_declined"))
    notifier = FakeNotifier()

    await processor_for(repository, gateway, notifier).process(payment.id)

    assert payment.status is PaymentStatus.FAILED
    assert payment.failure_reason == "card_declined"
    # Клиенту сообщают и про отказ тоже.
    assert len(notifier.calls) == 1


async def test_missing_payment_is_ignored() -> None:
    repository = FakeRepository(None)
    gateway = FakeGateway(ChargeResult(succeeded=True))
    notifier = FakeNotifier()

    await processor_for(repository, gateway, notifier).process(uuid.uuid4())

    assert gateway.calls == []
    assert notifier.calls == []


async def test_redelivery_does_not_charge_twice() -> None:
    """Главное свойство: доставка at-least-once не должна списывать деньги дважды."""
    payment = make_payment(status=PaymentStatus.SUCCEEDED, processed_at=utcnow())
    repository = FakeRepository(payment)
    gateway = FakeGateway(ChargeResult(succeeded=True))
    notifier = FakeNotifier()

    await processor_for(repository, gateway, notifier).process(payment.id)

    assert gateway.calls == []
    # Уведомление при этом всё равно уходит: до него в прошлый раз могло не дойти.
    assert len(notifier.calls) == 1


async def test_delivered_webhook_is_not_resent() -> None:
    payment = make_payment(
        status=PaymentStatus.SUCCEEDED, processed_at=utcnow(), webhook_delivered_at=utcnow()
    )
    repository = FakeRepository(payment)
    notifier = FakeNotifier()

    await processor_for(repository, FakeGateway(ChargeResult(succeeded=True)), notifier).process(
        payment.id
    )

    assert notifier.calls == []


async def test_lost_race_skips_webhook() -> None:
    """Нас опередил другой воркер: уведомляет он, а не мы."""
    payment = make_payment()
    repository = FakeRepository(payment, lost_race=True)
    notifier = FakeNotifier()

    await processor_for(repository, FakeGateway(ChargeResult(succeeded=True)), notifier).process(
        payment.id
    )

    assert notifier.calls == []


async def test_webhook_failure_propagates_for_message_retry() -> None:
    """Исключение должно долететь до RetryMiddleware, иначе сообщение не переотправят."""
    payment = make_payment()
    repository = FakeRepository(payment)
    notifier = FakeNotifier(error=RuntimeError("webhook недоступен"))

    with pytest.raises(RuntimeError):
        await processor_for(
            repository, FakeGateway(ChargeResult(succeeded=True)), notifier
        ).process(payment.id)

    # Платёж всё равно обработан: повторная доставка не пойдёт в шлюз снова.
    assert payment.status is PaymentStatus.SUCCEEDED
    assert repository.delivered_marks == 0
