import asyncio
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager, suppress
from typing import Protocol

import structlog
from faststream.rabbit import RabbitBroker

from payment_service.config import OutboxSettings
from payment_service.models import OutboxMessage

log = structlog.get_logger(__name__)


class OutboxStore(Protocol):
    async def fetch_unpublished(self, limit: int) -> Sequence[OutboxMessage]: ...

    def mark_published(self, message: OutboxMessage) -> None: ...

    def record_failure(self, message: OutboxMessage, error: str) -> None: ...


type OutboxTransaction = Callable[[], AbstractAsyncContextManager[OutboxStore]]


class OutboxRelay:
    """
    Вычитывает outbox и публикует события в брокер.

    Событие помечается опубликованным только после подтверждения от RabbitMQ,
    поэтому падение между publish и commit приводит к повторной публикации,
    а не к потере. Доставка получается at-least-once, и консьюмер обязан это учитывать.
    """

    def __init__(
        self,
        broker: RabbitBroker,
        outbox: OutboxTransaction,
        settings: OutboxSettings,
    ) -> None:
        self._broker = broker
        self._outbox = outbox
        self._settings = settings

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            published = await self.publish_batch()
            # Пачка заполнилась целиком, скорее всего есть ещё, идём за следующей
            # без паузы. В остальных случаях, включая полностью неудачную пачку, ждём.
            if published < self._settings.batch_size:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), self._settings.poll_interval_seconds)

    async def publish_batch(self) -> int:
        published = 0
        try:
            # Выход из транзакции коммитит отметки; исключение внутри - откатывает.
            async with self._outbox() as outbox:
                messages = await outbox.fetch_unpublished(self._settings.batch_size)
                for message in messages:
                    if await self._publish(message, outbox):
                        published += 1
        except Exception:
            log.exception("outbox.batch_failed")
            return 0
        return published

    async def _publish(self, message: OutboxMessage, outbox: OutboxStore) -> bool:
        try:
            await self._broker.publish(
                message.payload,
                exchange=message.exchange,
                routing_key=message.routing_key,
                # Сообщение переживёт рестарт брокера, а publisher confirm
                # дождётся записи на диск, прежде чем вернуть управление.
                persist=True,
                # id записи в outbox: по нему видно дубликат на стороне брокера.
                message_id=str(message.id),
                # Без таймаута зависший брокер держал бы открытую транзакцию
                # с блокировками записей до бесконечности.
                timeout=self._settings.publish_timeout_seconds,
            )
        except Exception as exc:
            outbox.record_failure(message, f"{type(exc).__name__}: {exc}")
            log.warning(
                "outbox.publish_failed",
                event_id=str(message.id),
                event_type=message.event_type,
                attempts=message.attempts,
                error=str(exc),
            )
            return False

        outbox.mark_published(message)
        log.info("outbox.published", event_id=str(message.id), event_type=message.event_type)
        return True
