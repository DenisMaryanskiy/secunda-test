import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from payment_service.config import OutboxSettings
from payment_service.models import OutboxMessage
from payment_service.models.base import utcnow
from payment_service.outbox.relay import OutboxRelay, OutboxStore


def make_message(**overrides: Any) -> OutboxMessage:
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "created_at": utcnow(),
        "aggregate_type": "payment",
        "aggregate_id": uuid.uuid4(),
        "event_type": "payment.created",
        "exchange": "payments",
        "routing_key": "payments.new",
        "payload": {"payment_id": str(uuid.uuid4())},
        "attempts": 0,
    }
    return OutboxMessage(**(fields | overrides))


class FakeBroker:
    def __init__(self, error: Exception | None = None, unroutable: str | None = None) -> None:
        self._error = error
        self._unroutable = unroutable
        self.published: list[dict[str, Any]] = []

    async def publish(self, payload: Any, **kwargs: Any) -> None:
        if self._error is not None:
            raise self._error
        if kwargs["routing_key"] == self._unroutable:
            msg = "нет маршрута"
            raise RuntimeError(msg)
        self.published.append({"payload": payload, **kwargs})


class FakeOutbox:
    """Хранилище в памяти с той же семантикой, что у настоящего репозитория."""

    def __init__(self, messages: Sequence[OutboxMessage]) -> None:
        self._messages = messages
        self.commits = 0

    async def fetch_unpublished(self, limit: int) -> Sequence[OutboxMessage]:
        pending = [message for message in self._messages if message.published_at is None]
        return pending[:limit]

    def mark_published(self, message: OutboxMessage) -> None:
        message.published_at = utcnow()

    def record_failure(self, message: OutboxMessage, error: str) -> None:
        message.attempts += 1
        message.last_error = error


def relay_for(outbox: FakeOutbox, broker: FakeBroker, **settings: Any) -> OutboxRelay:
    @asynccontextmanager
    async def transaction() -> AsyncIterator[OutboxStore]:
        yield outbox
        outbox.commits += 1

    return OutboxRelay(broker, transaction, OutboxSettings(**settings))  # type: ignore[arg-type]


async def test_publishes_pending_events() -> None:
    messages = [make_message(), make_message()]
    outbox, broker = FakeOutbox(messages), FakeBroker()

    published = await relay_for(outbox, broker).publish_batch()

    assert published == 2
    assert all(message.published_at is not None for message in messages)
    assert outbox.commits == 1


async def test_publish_carries_routing_and_message_id() -> None:
    message = make_message()
    broker = FakeBroker()

    await relay_for(FakeOutbox([message]), broker).publish_batch()

    sent = broker.published[0]
    assert sent["payload"] == message.payload
    assert sent["exchange"] == message.exchange
    assert sent["routing_key"] == message.routing_key
    # По message_id дубликат публикации видно на стороне брокера.
    assert sent["message_id"] == str(message.id)
    # Сообщение должно пережить рестарт брокера.
    assert sent["persist"] is True


async def test_failed_publish_leaves_event_unpublished() -> None:
    """Иначе событие было бы потеряно молча - ровно то, против чего затевался outbox."""
    message = make_message()
    outbox = FakeOutbox([message])
    broker = FakeBroker(error=ConnectionError("брокер недоступен"))

    published = await relay_for(outbox, broker).publish_batch()

    assert published == 0
    assert message.published_at is None
    assert message.attempts == 1
    assert "ConnectionError" in (message.last_error or "")
    # Отметка о неудаче при этом сохраняется.
    assert outbox.commits == 1


async def test_one_bad_event_does_not_block_the_rest() -> None:
    good, bad = make_message(), make_message(routing_key="некуда")
    broker = FakeBroker(unroutable="некуда")

    published = await relay_for(FakeOutbox([bad, good]), broker).publish_batch()

    assert published == 1
    assert good.published_at is not None
    assert bad.published_at is None


async def test_batch_failure_is_contained() -> None:
    """Упавшая транзакция не должна ронять цикл: relay обязан пережить недоступную БД."""

    class BrokenOutbox(FakeOutbox):
        async def fetch_unpublished(self, limit: int) -> Sequence[OutboxMessage]:
            msg = "БД недоступна"
            raise ConnectionError(msg)

    published = await relay_for(BrokenOutbox([]), FakeBroker()).publish_batch()

    assert published == 0


async def test_run_stops_on_event() -> None:
    stop = asyncio.Event()
    relay = relay_for(FakeOutbox([]), FakeBroker(), poll_interval_seconds=0.01)

    task = asyncio.create_task(relay.run(stop))
    await asyncio.sleep(0.05)
    stop.set()

    await asyncio.wait_for(task, timeout=1)
