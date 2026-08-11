import asyncio
import signal

import structlog

from payment_service.config import get_settings
from payment_service.db import create_engine, create_session_factory
from payment_service.logging import configure_logging
from payment_service.messaging.broker import create_broker, declare_topology
from payment_service.outbox.relay import OutboxRelay
from payment_service.repositories.outbox import outbox_transaction

log = structlog.get_logger("payment_service.outbox")


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
        relay = OutboxRelay(
            broker,
            outbox_transaction(create_session_factory(engine)),
            settings.outbox,
        )
        log.info("relay.started", poll_interval=settings.outbox.poll_interval_seconds)
        try:
            await relay.run(stop)
        finally:
            log.info("relay.stopping")
    finally:
        await broker.stop()
        await engine.dispose()


asyncio.run(main())
