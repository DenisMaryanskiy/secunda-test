import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import aio_pika
import pytest
from alembic import command
from alembic.config import Config
from faststream.rabbit import RabbitBroker
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.rabbitmq import RabbitMqContainer
from testcontainers.core.docker_client import DockerClient

from payment_service.config import Settings
from payment_service.db import SessionFactory, create_engine, create_session_factory
from payment_service.messaging.broker import create_broker, declare_topology
from payment_service.messaging.topology import DLQ_QUEUE, PAYMENT_CREATED_QUEUE

# Docker Desktop на macOS держит сокет в домашней папке и смонтировать его
# во вспомогательный контейнер testcontainers не даёт. Симлинк /var/run/docker.sock
# монтируется нормально и на Linux указывает на тот же сокет.
DOCKER_SOCKET = Path("/var/run/docker.sock")
if "TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE" not in os.environ and DOCKER_SOCKET.exists():
    os.environ["TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE"] = str(DOCKER_SOCKET)


@pytest.fixture(scope="session", autouse=True)
def docker_is_running() -> None:
    """Без Docker интеграционные тесты должны пропускаться, а не падать пачкой ошибок."""
    try:
        DockerClient().client.ping()
    except Exception as exc:
        pytest.skip(f"Docker недоступен: {exc}")


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:17-alpine", driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def broker_url() -> Iterator[str]:
    with RabbitMqContainer("rabbitmq:4-alpine") as container:
        params = container.get_connection_params()
        yield f"amqp://{params.credentials.username}:{params.credentials.password}@{params.host}:{params.port}/"


@pytest.fixture(scope="session")
def migrated_database(postgres_url: str) -> str:
    config = Config("alembic.ini")
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", postgres_url.replace("%", "%%"))
    command.upgrade(config, "head")
    return postgres_url


@pytest.fixture
def settings(migrated_database: str, broker_url: str) -> Settings:
    return Settings(
        api_key="test-api-key",
        log_level="WARNING",
        database={"url": migrated_database},
        broker={"url": broker_url},
        gateway={"min_delay_seconds": 0, "max_delay_seconds": 0},
        webhook={
            "signing_secret": "test-signing-secret",
            "allow_insecure_targets": True,
            "retry_base_delay_seconds": 0.001,
        },
        outbox={"poll_interval_seconds": 0.05},
        consumer={"retry_base_delay_seconds": 0.2},
    )


@pytest.fixture
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(settings.database)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> SessionFactory:
    return create_session_factory(engine)


@pytest.fixture(autouse=True)
async def clean_tables(engine: AsyncEngine) -> AsyncIterator[None]:
    # Контейнер один на всю сессию, так что за собой убирает каждый тест.
    yield
    async with engine.begin() as connection:
        await connection.execute(text("truncate payments, outbox_messages"))


@pytest.fixture
async def amqp(settings: Settings) -> AsyncIterator[aio_pika.abc.AbstractConnection]:
    """
    Отдельное соединение для осмотра очередей, сервисный код его не трогает.

    Наружу отдаётся именно соединение: aio_pika кэширует объект очереди на канале,
    поэтому счётчик сообщений приходится читать каждый раз на новом канале.
    """
    connection = await aio_pika.connect_robust(str(settings.broker.url))
    yield connection
    await connection.close()


@pytest.fixture
async def broker(
    settings: Settings, amqp: aio_pika.abc.AbstractConnection
) -> AsyncIterator[RabbitBroker]:
    broker = create_broker(settings.broker)
    await broker.connect()
    await declare_topology(broker, settings.consumer)
    async with amqp.channel() as channel:
        for name in (PAYMENT_CREATED_QUEUE, DLQ_QUEUE):
            await (await channel.get_queue(name)).purge()
    yield broker
    await broker.stop()


@pytest.fixture
async def webhook_sink() -> AsyncIterator[tuple[str, list[bytes]]]:
    """Минимальный приёмник вебхуков: настоящий сокет, чтобы клиент ходил по-настоящему."""
    received: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        received.append(await reader.read(65536))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        yield f"http://127.0.0.1:{port}/hook", received
