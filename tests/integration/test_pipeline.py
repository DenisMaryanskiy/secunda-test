import asyncio
import uuid
from collections.abc import Awaitable, Callable
from functools import partial

import aio_pika
import httpx
import pytest
from faststream import AckPolicy, TestApp
from faststream.rabbit import RabbitBroker

from payment_service.adapters.gateway import ChargeResult
from payment_service.adapters.webhooks import HttpWebhookNotifier
from payment_service.config import Settings
from payment_service.db import SessionFactory
from payment_service.enums import PaymentStatus
from payment_service.messaging.broker import create_broker
from payment_service.messaging.consumer import create_consumer_app
from payment_service.messaging.events import PaymentCreatedEvent, payment_created_message
from payment_service.messaging.retry import ERROR_HEADER, RetryMiddleware
from payment_service.messaging.topology import (
    ATTEMPT_HEADER,
    DLQ_QUEUE,
    PAYMENT_CREATED_QUEUE,
    payment_created_queue,
    payments_exchange,
)
from payment_service.models import Payment
from payment_service.outbox.relay import OutboxRelay
from payment_service.repositories.outbox import OutboxRepository, outbox_transaction
from payment_service.repositories.payments import PaymentRepository, payments_transaction
from payment_service.services.processing import PaymentProcessor
from tests.factories import make_payment

pytestmark = pytest.mark.integration


class StubGateway:
    def __init__(self, result: ChargeResult) -> None:
        self._result = result
        self.calls = 0

    async def charge(self, payment_id: uuid.UUID) -> ChargeResult:
        self.calls += 1
        return self._result


async def depth(connection: aio_pika.abc.AbstractConnection, name: str) -> int:
    # Каждый раз новый канал: на старом aio_pika вернёт закэшированный счётчик.
    async with connection.channel() as channel:
        queue = await channel.declare_queue(name, durable=True, passive=True)
        return int(queue.declaration_result.message_count or 0)


async def has_messages(
    connection: aio_pika.abc.AbstractConnection, name: str, expected: int
) -> bool:
    return await depth(connection, name) == expected


async def eventually(check: Callable[[], Awaitable[bool]], seconds: float = 15.0) -> None:
    """Ждёт условия: очереди и консьюмер работают асинхронно, мгновенных проверок тут нет."""
    try:
        async with asyncio.timeout(seconds):
            # ASYNC110 предлагает asyncio.Event, но состояние тут внешнее
            # глубина очереди и запись в БД, событию взяться неоткуда.
            while not await check():  # noqa: ASYNC110
                await asyncio.sleep(0.1)
    except TimeoutError:
        pytest.fail(f"условие не выполнилось за {seconds} с")


async def store_payment(
    session_factory: SessionFactory, payment: Payment, routing_key: str | None = None
) -> None:
    async with session_factory() as session:
        PaymentRepository(session).add(payment)
        await session.flush()
        message = payment_created_message(payment)
        if routing_key is not None:
            message.routing_key = routing_key
        OutboxRepository(session).add(message)
        await session.commit()


async def publish_outbox(
    broker: RabbitBroker, session_factory: SessionFactory, settings: Settings
) -> int:
    relay = OutboxRelay(broker, outbox_transaction(session_factory), settings.outbox)
    return await relay.publish_batch()


async def test_relay_delivers_event_to_the_queue(
    broker: RabbitBroker,
    amqp: aio_pika.abc.AbstractConnection,
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    await store_payment(session_factory, make_payment())

    published = await publish_outbox(broker, session_factory, settings)

    assert published == 1
    async with session_factory() as session:
        assert await OutboxRepository(session).fetch_unpublished(10) == []
    await eventually(lambda: has_messages(amqp, PAYMENT_CREATED_QUEUE, 1))


async def test_unroutable_event_is_not_marked_published(
    broker: RabbitBroker, session_factory: SessionFactory, settings: Settings
) -> None:
    """Брокер возвращает такое сообщение молча, без on_return_raises оно бы потерялось."""
    await store_payment(session_factory, make_payment(), routing_key="маршрута-нет")

    published = await publish_outbox(broker, session_factory, settings)

    assert published == 0
    async with session_factory() as session:
        [pending] = await OutboxRepository(session).fetch_unpublished(10)
    assert pending.published_at is None
    assert pending.attempts == 1
    assert "PublishError" in (pending.last_error or "")


async def test_processing_updates_payment_and_signs_webhook(
    session_factory: SessionFactory, settings: Settings
) -> None:
    payment = make_payment()
    await store_payment(session_factory, payment)
    received: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(200)

    notifier = HttpWebhookNotifier(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)), settings.webhook
    )
    processor = PaymentProcessor(
        payments_transaction(session_factory), StubGateway(ChargeResult(succeeded=True)), notifier
    )

    await processor.process(payment.id)

    async with session_factory() as session:
        stored = await session.get(Payment, payment.id)
    assert stored is not None
    assert stored.status is PaymentStatus.SUCCEEDED
    assert stored.processed_at is not None
    assert stored.webhook_delivered_at is not None
    assert len(received) == 1
    assert received[0].headers["X-Payment-Signature"].startswith("sha256=")


async def test_redelivery_does_not_charge_or_notify_twice(
    session_factory: SessionFactory, settings: Settings
) -> None:
    """Доставка at-least-once не должна оборачиваться повторным списанием."""
    payment = make_payment()
    await store_payment(session_factory, payment)
    notifications = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal notifications
        notifications += 1
        return httpx.Response(200)

    gateway = StubGateway(ChargeResult(succeeded=True))
    processor = PaymentProcessor(
        payments_transaction(session_factory),
        gateway,
        HttpWebhookNotifier(
            httpx.AsyncClient(transport=httpx.MockTransport(handler)), settings.webhook
        ),
    )

    await processor.process(payment.id)
    await processor.process(payment.id)

    assert gateway.calls == 1
    assert notifications == 1


async def test_failing_handler_walks_retry_queues_into_dlq(
    broker: RabbitBroker,
    amqp: aio_pika.abc.AbstractConnection,
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    await store_payment(session_factory, make_payment())
    await publish_outbox(broker, session_factory, settings)

    consumer = create_broker(settings.broker)
    consumer.add_middleware(partial(RetryMiddleware, broker=consumer, settings=settings.consumer))
    attempts = 0

    @consumer.subscriber(
        payment_created_queue, payments_exchange, ack_policy=AckPolicy.REJECT_ON_ERROR
    )
    async def always_fails(event: PaymentCreatedEvent) -> None:
        nonlocal attempts
        attempts += 1
        msg = "обработчик всегда падает"
        raise RuntimeError(msg)

    await consumer.start()
    try:
        await eventually(lambda: has_messages(amqp, DLQ_QUEUE, 1))
    finally:
        await consumer.stop()

    assert attempts == settings.consumer.max_attempts
    async with amqp.channel() as channel:
        dead = await (await channel.get_queue(DLQ_QUEUE)).get()
        assert dead.headers[ATTEMPT_HEADER] == settings.consumer.max_attempts
        assert "RuntimeError" in str(dead.headers[ERROR_HEADER])
        await dead.ack()


async def test_consumer_app_handles_a_real_message(
    broker: RabbitBroker,
    session_factory: SessionFactory,
    settings: Settings,
    webhook_sink: tuple[str, list[bytes]],
) -> None:
    """Настоящая сборка консьюмера: топология, middleware, шлюз и доставка webhook."""
    url, received = webhook_sink
    payment = make_payment(webhook_url=url)
    await store_payment(session_factory, payment)
    await publish_outbox(broker, session_factory, settings)

    async def processed() -> bool:
        async with session_factory() as session:
            stored = await session.get(Payment, payment.id)
            return stored is not None and stored.webhook_delivered_at is not None

    async with TestApp(create_consumer_app(settings)):
        await eventually(processed)

    assert received
    assert b"X-Payment-Signature" in received[0]
