from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from payment_service.config import DatabaseSettings, GatewaySettings, Settings, get_settings

ENV = {
    "API_KEY": "from-env",
    "DATABASE__URL": "postgresql+asyncpg://user:pass@db:5432/payments",
    "BROKER__URL": "amqp://guest:guest@rabbit:5672/",
    "WEBHOOK__SIGNING_SECRET": "secret-from-env",
}


def test_secrets_do_not_leak_into_repr(settings: Settings) -> None:
    """Секреты попадают в трейсбеки и дампы конфига, если хранить их строкой."""
    dumped = repr(settings)

    assert "test-api-key" not in dumped
    assert "test-signing-secret" not in dumped
    assert settings.api_key.get_secret_value() == "test-api-key"


def test_safe_url_hides_password() -> None:
    database = DatabaseSettings(url="postgresql+asyncpg://user:s3cret@db:5432/payments")

    assert database.safe_url == "postgresql+asyncpg://db:5432/payments"
    assert "s3cret" not in database.safe_url


def test_delay_range_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="min_delay_seconds"):
        GatewaySettings(min_delay_seconds=5, max_delay_seconds=2)


def test_insecure_webhooks_are_forbidden_in_production() -> None:
    with pytest.raises(ValidationError, match="production"):
        Settings(
            environment="production",
            api_key="k",
            database={"url": "postgresql+asyncpg://u:p@db:5432/d"},
            broker={"url": "amqp://guest:guest@rabbit:5672/"},
            webhook={"signing_secret": "s", "allow_insecure_targets": True},
        )


@pytest.fixture
def clean_settings_cache() -> Iterator[None]:
    # Иначе упавшая проверка оставит следующим тестам конфиг из чужого окружения.
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.usefixtures("clean_settings_cache")
def test_nested_settings_come_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("CONSUMER__MAX_ATTEMPTS", "5")

    settings = get_settings()

    assert settings.api_key.get_secret_value() == "from-env"
    assert settings.consumer.max_attempts == 5
    # Настройки читаются один раз на процесс.
    assert get_settings() is settings
