import pytest

from payment_service.config import ConsumerSettings
from payment_service.messaging.topology import (
    PAYMENT_CREATED_QUEUE,
    PAYMENT_CREATED_ROUTING_KEY,
    PAYMENTS_EXCHANGE,
    dlq_queue,
    payment_created_queue,
    retry_delay_ms,
    retry_queues,
    retry_routing_key,
)


@pytest.mark.parametrize(
    "attempt, expected_ms",
    [(1, 2000), (2, 4000), (3, 8000), (4, 16000)],
)
def test_delay_doubles_each_attempt(attempt: int, expected_ms: int) -> None:
    settings = ConsumerSettings(retry_base_delay_seconds=2.0)

    assert retry_delay_ms(attempt, settings) == expected_ms


def test_delay_follows_configured_base() -> None:
    settings = ConsumerSettings(retry_base_delay_seconds=0.5)

    assert retry_delay_ms(1, settings) == 500
    assert retry_delay_ms(2, settings) == 1000


def test_one_retry_queue_per_attempt_except_the_last() -> None:
    """После последней попытки сообщение уходит в DLQ, а не в очередь ожидания."""
    queues = retry_queues(ConsumerSettings(max_attempts=3))

    assert [queue.name for queue in queues] == [
        "payments.new.retry.1",
        "payments.new.retry.2",
    ]


def test_single_attempt_means_no_retry_queues() -> None:
    assert retry_queues(ConsumerSettings(max_attempts=1)) == []


def test_retry_queue_returns_message_to_the_main_queue() -> None:
    queue = retry_queues(ConsumerSettings(max_attempts=2))[0]

    assert queue.arguments is not None
    # Протухнув по TTL, сообщение по DLX возвращается в основную очередь.
    assert queue.arguments["x-message-ttl"] == 2000
    assert queue.arguments["x-dead-letter-exchange"] == PAYMENTS_EXCHANGE
    assert queue.arguments["x-dead-letter-routing-key"] == PAYMENT_CREATED_ROUTING_KEY
    assert queue.routing_key == retry_routing_key(1)


def test_main_queue_has_dlx_as_a_safety_net() -> None:
    """На случай, если сообщение отвергнет сам брокер, минуя нашу логику ретраев."""
    assert payment_created_queue.arguments is not None
    assert payment_created_queue.arguments["x-dead-letter-exchange"] == "payments.dlx"
    assert payment_created_queue.name == PAYMENT_CREATED_QUEUE


def test_dlq_is_bound_by_the_original_routing_key() -> None:
    assert dlq_queue.routing_key == PAYMENT_CREATED_ROUTING_KEY
