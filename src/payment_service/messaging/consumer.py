from functools import partial

import structlog
from faststream import AckPolicy, FastStream
from faststream.rabbit import Channel

from payment_service.config import Settings, get_settings
from payment_service.db import create_engine, create_session_factory
from payment_service.logging import configure_logging
from payment_service.messaging.broker import create_broker, declare_topology
from payment_service.messaging.events import PaymentCreatedEvent
from payment_service.messaging.retry import RetryMiddleware
from payment_service.messaging.topology import payment_created_queue, payments_exchange
from payment_service.repositories.payments import payments_transaction
from payment_service.services.gateway import EmulatedPaymentGateway
from payment_service.services.processing import PaymentProcessor
from payment_service.services.webhooks import HttpWebhookNotifier, create_webhook_client

log = structlog.get_logger("payment_service.messaging.consumer")


def create_app(settings: Settings | None = None) -> FastStream:
    settings = settings or get_settings()
    configure_logging(settings)

    broker = create_broker(settings.broker)
    broker.add_middleware(partial(RetryMiddleware, broker=broker, settings=settings.consumer))

    engine = create_engine(settings.database)
    webhook_client = create_webhook_client(settings.webhook)
    processor = PaymentProcessor(
        payments_transaction(create_session_factory(engine)),
        EmulatedPaymentGateway(settings.gateway),
        HttpWebhookNotifier(webhook_client, settings.webhook),
    )

    @broker.subscriber(
        payment_created_queue,
        payments_exchange,
        channel=Channel(prefetch_count=settings.consumer.prefetch_count),
        # Логику повторов держит RetryMiddleware. Сюда исключение долетает,
        # только если сорвалась сама перекладка, и тогда reject уводит сообщение
        # в DLQ через x-dead-letter-exchange основной очереди.
        ack_policy=AckPolicy.REJECT_ON_ERROR,
    )
    async def handle_payment_created(event: PaymentCreatedEvent) -> None:
        structlog.contextvars.bind_contextvars(
            payment_id=str(event.payment_id), event_id=str(event.event_id)
        )
        try:
            await processor.process(event.payment_id)
        finally:
            structlog.contextvars.clear_contextvars()

    app = FastStream(broker, logger=None)

    @app.on_startup
    async def prepare() -> None:
        # Топология объявляется до подписки: если первое же сообщение упадёт,
        # retry-очереди уже должны существовать.
        await broker.connect()
        await declare_topology(broker, settings.consumer)
        log.info("consumer.started", prefetch=settings.consumer.prefetch_count)

    @app.after_shutdown
    async def cleanup() -> None:
        await webhook_client.aclose()
        await engine.dispose()
        log.info("consumer.stopped")

    return app


if __name__ == "__main__":
    import asyncio

    asyncio.run(create_app().run())
