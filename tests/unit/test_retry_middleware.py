from dataclasses import dataclass, field
from typing import Any

import pytest

from payment_service.config import ConsumerSettings
from payment_service.messaging.retry import (
    ERROR_HEADER,
    MAX_ERROR_HEADER_LENGTH,
    RetryMiddleware,
)
from payment_service.messaging.topology import ATTEMPT_HEADER, DLX_EXCHANGE, RETRY_EXCHANGE


@dataclass
class Published:
    body: bytes
    exchange: str
    routing_key: str
    headers: dict[str, Any]
    message_id: str | None


@dataclass
class FakeBroker:
    published: list[Published] = field(default_factory=list)

    async def publish(
        self,
        body: bytes,
        *,
        exchange: str,
        routing_key: str,
        headers: dict[str, Any],
        message_id: str | None = None,
        **_: Any,
    ) -> None:
        self.published.append(Published(body, exchange, routing_key, headers, message_id))


@dataclass
class FakeMessage:
    body: bytes = b'{"payment_id": "x"}'
    headers: dict[str, Any] = field(default_factory=dict)
    message_id: str | None = "msg-1"


def middleware_for(broker: FakeBroker, **settings: Any) -> RetryMiddleware:
    return RetryMiddleware(
        None,
        context=None,
        broker=broker,  # type: ignore[arg-type]
        settings=ConsumerSettings(**settings),
    )


async def run(middleware: RetryMiddleware, msg: FakeMessage, error: Exception | None) -> Any:
    async def call_next(_: Any) -> str:
        if error is not None:
            raise error
        return "ok"

    return await middleware.consume_scope(call_next, msg)  # type: ignore[arg-type]


async def test_successful_message_is_not_republished() -> None:
    broker = FakeBroker()

    result = await run(middleware_for(broker), FakeMessage(), error=None)

    assert result == "ok"
    assert broker.published == []


async def test_first_failure_goes_to_the_first_retry_queue() -> None:
    broker = FakeBroker()

    await run(middleware_for(broker), FakeMessage(), error=RuntimeError("нет связи"))

    published = broker.published[0]
    assert published.exchange == RETRY_EXCHANGE
    assert published.routing_key == "1"
    assert published.headers[ATTEMPT_HEADER] == 2
    assert "RuntimeError: нет связи" in published.headers[ERROR_HEADER]


async def test_attempt_counter_advances_the_retry_queue() -> None:
    broker = FakeBroker()
    msg = FakeMessage(headers={ATTEMPT_HEADER: 2})

    await run(middleware_for(broker), msg, error=RuntimeError("снова"))

    assert broker.published[0].routing_key == "2"
    assert broker.published[0].headers[ATTEMPT_HEADER] == 3


async def test_last_attempt_goes_to_dlq() -> None:
    broker = FakeBroker()
    msg = FakeMessage(headers={ATTEMPT_HEADER: 3})

    await run(middleware_for(broker, max_attempts=3), msg, error=RuntimeError("всё"))

    published = broker.published[0]
    assert published.exchange == DLX_EXCHANGE
    assert published.headers[ATTEMPT_HEADER] == 3


async def test_exception_is_swallowed_so_the_message_gets_acked() -> None:
    """Пропусти мы исключение дальше, сообщение уехало бы в DLQ мимо счётчика попыток."""
    broker = FakeBroker()

    result = await run(middleware_for(broker), FakeMessage(), error=RuntimeError("ошибка"))

    assert result is None


async def test_message_id_survives_rescheduling() -> None:
    broker = FakeBroker()

    await run(middleware_for(broker), FakeMessage(message_id="event-42"), error=RuntimeError("x"))

    assert broker.published[0].message_id == "event-42"


async def test_body_is_forwarded_unchanged() -> None:
    broker = FakeBroker()
    msg = FakeMessage(body=b'{"payment_id": "abc"}')

    await run(middleware_for(broker), msg, error=RuntimeError("x"))

    assert broker.published[0].body == msg.body


async def test_long_error_is_truncated_for_the_header() -> None:
    """Ошибка валидации Pydantic разворачивается на килобайты, а это заголовок AMQP."""
    broker = FakeBroker()

    await run(middleware_for(broker), FakeMessage(), error=RuntimeError("ы" * 5000))

    assert len(broker.published[0].headers[ERROR_HEADER]) == MAX_ERROR_HEADER_LENGTH


async def test_republish_failure_propagates() -> None:
    """Если переложить не вышло, пусть падает: reject уведёт сообщение в DLQ по DLX."""

    class BrokenBroker(FakeBroker):
        async def publish(self, *_: Any, **__: Any) -> None:
            msg = "брокер недоступен"
            raise ConnectionError(msg)

    with pytest.raises(ConnectionError):
        await run(middleware_for(BrokenBroker()), FakeMessage(), error=RuntimeError("x"))
