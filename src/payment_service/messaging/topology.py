"""
Топология RabbitMQ. Единственное место, где эти имена определяются.

Задержка между повторами делается связкой TTL + dead-letter exchange: сообщение
кладётся в очередь payments.new.retry.N, лежит там x-message-ttl миллисекунд,
протухает и по DLX возвращается в payments.new. Очередь на каждый шаг нужна
потому, что TTL задаётся на очередь целиком, а задержка растёт экспоненциально.
"""

from faststream.rabbit import ExchangeType, RabbitExchange, RabbitQueue

from payment_service.config import ConsumerSettings

PAYMENTS_EXCHANGE = "payments"
RETRY_EXCHANGE = "payments.retry"
DLX_EXCHANGE = "payments.dlx"

PAYMENT_CREATED_ROUTING_KEY = "payments.new"
PAYMENT_CREATED_QUEUE = "payments.new"
DLQ_QUEUE = "payments.new.dlq"

ATTEMPT_HEADER = "x-attempt"

payments_exchange = RabbitExchange(PAYMENTS_EXCHANGE, type=ExchangeType.DIRECT, durable=True)
retry_exchange = RabbitExchange(RETRY_EXCHANGE, type=ExchangeType.DIRECT, durable=True)
dlx_exchange = RabbitExchange(DLX_EXCHANGE, type=ExchangeType.DIRECT, durable=True)

payment_created_queue = RabbitQueue(
    PAYMENT_CREATED_QUEUE,
    durable=True,
    routing_key=PAYMENT_CREATED_ROUTING_KEY,
    # Страховка на случай, если сообщение отвергнет сам брокер, минуя нашу логику ретраев.
    arguments={"x-dead-letter-exchange": DLX_EXCHANGE},
)

dlq_queue = RabbitQueue(
    DLQ_QUEUE,
    durable=True,
    routing_key=PAYMENT_CREATED_ROUTING_KEY,
)


def retry_delay_ms(attempt: int, settings: ConsumerSettings) -> int:
    """Экспоненциальная задержка перед попыткой номер attempt + 1."""
    return int(settings.retry_base_delay_seconds * 1000 * 2 ** (attempt - 1))


def retry_routing_key(attempt: int) -> str:
    return str(attempt)


def retry_queues(settings: ConsumerSettings) -> list[RabbitQueue]:
    """По очереди на каждую неудачную попытку, кроме последней - после неё DLQ."""
    return [
        RabbitQueue(
            f"{PAYMENT_CREATED_QUEUE}.retry.{attempt}",
            durable=True,
            routing_key=retry_routing_key(attempt),
            arguments={
                "x-message-ttl": retry_delay_ms(attempt, settings),
                "x-dead-letter-exchange": PAYMENTS_EXCHANGE,
                "x-dead-letter-routing-key": PAYMENT_CREATED_ROUTING_KEY,
            },
        )
        for attempt in range(1, settings.max_attempts)
    ]
