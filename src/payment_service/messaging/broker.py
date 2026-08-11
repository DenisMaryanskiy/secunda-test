import logging

import structlog
from faststream.rabbit import Channel, RabbitBroker

from payment_service.config import BrokerSettings, ConsumerSettings
from payment_service.messaging.topology import (
    PAYMENT_CREATED_ROUTING_KEY,
    dlq_queue,
    dlx_exchange,
    payment_created_queue,
    payments_exchange,
    retry_exchange,
    retry_queues,
)

log = structlog.get_logger(__name__)


def create_broker(settings: BrokerSettings) -> RabbitBroker:
    return RabbitBroker(
        str(settings.url),
        graceful_timeout=10.0,
        logger=logging.getLogger("payment_service.faststream"),
        # По умолчанию немаршрутизируемое сообщение брокер молча возвращает
        # через Basic.Return, и publish завершается успешно, событие теряется.
        # С on_return_raises оно превращается в исключение.
        default_channel=Channel(publisher_confirms=True, on_return_raises=True),
    )


async def declare_topology(broker: RabbitBroker, settings: ConsumerSettings) -> None:
    """
    Объявляет и связывает обменники с очередями.

    Подписчик поднимает только то, что читает сам, поэтому retry-очереди и DLQ
    создаются здесь: консьюмеров у них нет, а существовать к моменту первой
    ошибки они обязаны. declare_queue при этом очередь не привязывает,
    bind приходится делать руками.
    """
    payments = await broker.declare_exchange(payments_exchange)
    retry = await broker.declare_exchange(retry_exchange)
    dlx = await broker.declare_exchange(dlx_exchange)

    main = await broker.declare_queue(payment_created_queue)
    await main.bind(payments, routing_key=PAYMENT_CREATED_ROUTING_KEY)

    dlq = await broker.declare_queue(dlq_queue)
    await dlq.bind(dlx, routing_key=PAYMENT_CREATED_ROUTING_KEY)

    for queue in retry_queues(settings):
        declared = await broker.declare_queue(queue)
        await declared.bind(retry, routing_key=queue.routing_key)

    log.info("topology.declared", retry_levels=settings.max_attempts - 1)
