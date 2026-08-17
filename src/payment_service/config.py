from functools import lru_cache
from typing import Literal, Self

from pydantic import AmqpDsn, BaseModel, Field, PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Окружения, где публикуется схема API.
DOCS_ENVIRONMENTS = frozenset({"local"})


class DatabaseSettings(BaseModel):
    url: PostgresDsn
    echo: bool = False
    pool_size: int = Field(default=10, ge=1)
    max_overflow: int = Field(default=5, ge=0)

    @property
    def safe_url(self) -> str:
        """URL без пароля - единственная форма, пригодная для логов."""
        host = self.url.hosts()[0]
        return f"{self.url.scheme}://{host['host']}:{host['port']}{self.url.path or ''}"


class BrokerSettings(BaseModel):
    url: AmqpDsn


class ConsumerSettings(BaseModel):
    prefetch_count: int = Field(default=10, ge=1)
    # Попыток на сообщение всего; после последней оно уезжает в DLQ.
    max_attempts: int = Field(default=3, ge=1)
    # Задержка перед повтором: base * 2^(attempt-1).
    retry_base_delay_seconds: float = Field(default=2.0, gt=0)


class GatewaySettings(BaseModel):
    """Эмуляция внешнего платёжного шлюза."""

    min_delay_seconds: float = Field(default=2.0, ge=0)
    max_delay_seconds: float = Field(default=5.0, ge=0)
    success_rate: float = Field(default=0.9, ge=0, le=1)

    @model_validator(mode="after")
    def check_delay_range(self) -> Self:
        if self.min_delay_seconds > self.max_delay_seconds:
            msg = "min_delay_seconds не может быть больше max_delay_seconds"
            raise ValueError(msg)
        return self


class WebhookSettings(BaseModel):
    signing_secret: SecretStr
    timeout_seconds: float = Field(default=5.0, gt=0)
    max_attempts: int = Field(default=3, ge=1)
    retry_base_delay_seconds: float = Field(default=0.5, gt=0)
    allow_insecure_targets: bool = Field(
        default=False,
        description="Разрешить http и приватные адреса. Только для локальной разработки.",
    )


class OutboxSettings(BaseModel):
    poll_interval_seconds: float = Field(default=1.0, gt=0)
    batch_size: int = Field(default=100, ge=1)
    publish_timeout_seconds: float = Field(default=5.0, gt=0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    environment: Literal["local", "production"] = "local"
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"

    api_key: SecretStr

    database: DatabaseSettings
    broker: BrokerSettings
    consumer: ConsumerSettings = ConsumerSettings()
    gateway: GatewaySettings = GatewaySettings()
    webhook: WebhookSettings
    outbox: OutboxSettings = OutboxSettings()

    @property
    def docs_enabled(self) -> bool:
        return self.environment in DOCS_ENVIRONMENTS

    @model_validator(mode="after")
    def forbid_insecure_webhooks_in_production(self) -> Self:
        if self.environment == "production" and self.webhook.allow_insecure_targets:
            msg = "WEBHOOK__ALLOW_INSECURE_TARGETS нельзя включать в production"
            raise ValueError(msg)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
