import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from payment_service.db import SessionFactory
from payment_service.enums import Currency, PaymentStatus
from payment_service.messaging.events import payment_created_message
from payment_service.models import OutboxMessage, Payment
from payment_service.repositories.outbox import OutboxRepository
from payment_service.repositories.payments import PaymentRepository
from payment_service.services.commands import NewPayment
from payment_service.services.payments import CreatedPayment, PaymentService
from tests.factories import make_payment

pytestmark = pytest.mark.integration


async def test_finish_processing_is_a_one_time_transition(
    session_factory: SessionFactory,
) -> None:
    """Защита от повторной доставки: второй раз обновить нечего."""
    payment = make_payment()
    async with session_factory() as session:
        PaymentRepository(session).add(payment)
        await session.commit()

    async with session_factory() as session:
        first = await PaymentRepository(session).finish_processing(
            payment.id, status=PaymentStatus.SUCCEEDED, failure_reason=None
        )
        await session.commit()

    async with session_factory() as session:
        second = await PaymentRepository(session).finish_processing(
            payment.id, status=PaymentStatus.FAILED, failure_reason="поздно"
        )
        await session.commit()

    assert first is not None
    assert first.status is PaymentStatus.SUCCEEDED
    assert first.processed_at is not None
    assert second is None


async def test_finish_processing_records_failure_reason(session_factory: SessionFactory) -> None:
    payment = make_payment()
    async with session_factory() as session:
        PaymentRepository(session).add(payment)
        await session.commit()

    async with session_factory() as session:
        updated = await PaymentRepository(session).finish_processing(
            payment.id, status=PaymentStatus.FAILED, failure_reason="card_declined"
        )
        await session.commit()

    assert updated is not None
    assert updated.status is PaymentStatus.FAILED
    assert updated.failure_reason == "card_declined"


async def test_lookup_by_idempotency_key(session_factory: SessionFactory) -> None:
    payment = make_payment(idempotency_key="ключ-1")
    async with session_factory() as session:
        PaymentRepository(session).add(payment)
        await session.commit()

    async with session_factory() as session:
        repository = PaymentRepository(session)
        found = await repository.get_by_idempotency_key("ключ-1")
        missing = await repository.get_by_idempotency_key("ключ-2")

    assert found is not None
    assert found.id == payment.id
    assert missing is None


async def test_skip_locked_lets_relays_work_in_parallel(session_factory: SessionFactory) -> None:
    """Без SKIP LOCKED второй relay ждал бы на занятых записях вместо работы."""
    async with session_factory() as session:
        for _ in range(4):
            payment = make_payment()
            PaymentRepository(session).add(payment)
            await session.flush()
            OutboxRepository(session).add(payment_created_message(payment))
        await session.commit()

    async def take(limit: int) -> list[str]:
        async with session_factory() as session:
            messages = await OutboxRepository(session).fetch_unpublished(limit)
            # Держим блокировку, пока соседний relay делает свою выборку.
            await asyncio.sleep(0.3)
            return [str(message.id) for message in messages]

    first, second = await asyncio.gather(take(2), take(2))

    assert len(first) == 2
    assert len(second) == 2
    # Пересечений быть не должно: две пачки разошлись по разным записям.
    assert set(first).isdisjoint(second)


async def test_published_events_are_not_fetched_again(session_factory: SessionFactory) -> None:
    async with session_factory() as session:
        payment = make_payment()
        PaymentRepository(session).add(payment)
        await session.flush()
        OutboxRepository(session).add(payment_created_message(payment))
        await session.commit()

    async with session_factory() as session:
        repository = OutboxRepository(session)
        [message] = await repository.fetch_unpublished(10)
        repository.mark_published(message)
        await session.commit()

    async with session_factory() as session:
        assert await OutboxRepository(session).fetch_unpublished(10) == []


async def test_failure_is_recorded_on_the_event(session_factory: SessionFactory) -> None:
    async with session_factory() as session:
        payment = make_payment()
        PaymentRepository(session).add(payment)
        await session.flush()
        OutboxRepository(session).add(payment_created_message(payment))
        await session.commit()

    async with session_factory() as session:
        repository = OutboxRepository(session)
        [message] = await repository.fetch_unpublished(10)
        repository.record_failure(message, "ConnectionError: брокер недоступен")
        await session.commit()

    async with session_factory() as session:
        [message] = await OutboxRepository(session).fetch_unpublished(10)

    assert message.attempts == 1
    assert message.last_error is not None
    assert "брокер недоступен" in message.last_error


async def test_concurrent_requests_with_one_key_create_one_payment(
    session_factory: SessionFactory,
) -> None:
    """Проверка "нет ли уже такого" не спасает от гонки, спасает unique-констрейнт."""
    command = NewPayment(
        amount=Decimal("10.00"),
        currency=Currency.RUB,
        description="одновременно",
        metadata={},
        webhook_url="https://example.com/hook",
    )

    async def create() -> CreatedPayment:
        async with session_factory() as session:
            return await PaymentService(session).create(command, "гоночный-ключ")

    first, second = await asyncio.gather(create(), create())

    assert first.payment.id == second.payment.id
    # Один из запросов обязан узнать, что он повтор.
    assert first.replayed != second.replayed
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Payment)) == 1
        assert await session.scalar(select(func.count()).select_from(OutboxMessage)) == 1
