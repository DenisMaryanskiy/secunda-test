import asyncio
import signal
from contextlib import suppress

import structlog
from faststream.rabbit import RabbitBroker

from payment_service.config import OutboxSettings, get_settings
from payment_service.db import SessionFactory, create_engine, create_session_factory
from payment_service.logging import configure_logging
from payment_service.messaging.broker import create_broker, declare_topology
from payment_service.models import OutboxMessage
from payment_service.repositories.outbox import OutboxRepository

log = structlog.get_logger("payment_service.outbox.relay")


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
        session_factory: SessionFactory,
        settings: OutboxSettings,
    ) -> None:
        self._broker = broker
        self._session_factory = session_factory
        self._settings = settings

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            published = await self._publish_batch()
            # Пачка заполнилась целиком, скорее всего есть ещё, идём за следующей
            # без паузы. В остальных случаях, включая полностью неудачную пачку, ждём.
            if published < self._settings.batch_size:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), self._settings.poll_interval_seconds)

    async def _publish_batch(self) -> int:
        published = 0
        async with self._session_factory() as session:
            repository = OutboxRepository(session)
            try:
                messages = await repository.fetch_unpublished(self._settings.batch_size)
                for message in messages:
                    if await self._publish(message, repository):
                        published += 1
                await session.commit()
            except Exception:
                await session.rollback()
                log.exception("outbox.batch_failed")
                return 0
        return published

    async def _publish(self, message: OutboxMessage, repository: OutboxRepository) -> bool:
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
            repository.record_failure(message, f"{type(exc).__name__}: {exc}")
            log.warning(
                "outbox.publish_failed",
                event_id=str(message.id),
                event_type=message.event_type,
                attempts=message.attempts,
                error=str(exc),
            )
            return False

        repository.mark_published(message)
        log.info("outbox.published", event_id=str(message.id), event_type=message.event_type)
        return True


async def main() -> None:
    settings = get_settings()
    configure_logging(settings)

    engine = create_engine(settings.database)
    broker = create_broker(settings.broker)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await broker.connect()
    try:
        await declare_topology(broker, settings.consumer)
        relay = OutboxRelay(broker, create_session_factory(engine), settings.outbox)
        log.info("relay.started", poll_interval=settings.outbox.poll_interval_seconds)
        try:
            await relay.run(stop)
        finally:
            log.info("relay.stopping")
    finally:
        await broker.stop()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
