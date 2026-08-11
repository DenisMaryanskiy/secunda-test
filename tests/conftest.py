import pytest

from payment_service.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        api_key="test-api-key",
        database={"url": "postgresql+asyncpg://test:test@localhost:5432/test"},
        broker={"url": "amqp://guest:guest@localhost:5672/"},
        webhook={"signing_secret": "test-signing-secret"},
    )
