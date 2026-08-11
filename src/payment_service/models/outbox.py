import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from payment_service.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class OutboxMessage(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """
    Событие, ожидающее публикации в брокер.

    Пишется в одной транзакции с изменением бизнес-данных, поэтому состояние
    "платёж создан, но событие потерялось" невозможно. Публикацией занимается
    отдельный процесс (outbox relay), доставка получается at-least-once.
    """

    __tablename__ = "outbox_messages"
    __table_args__ = (
        # Relay читает только неопубликованные, частичный индекс не растёт вместе с таблицей.
        Index(
            "ix_outbox_messages_unpublished",
            "created_at",
            postgresql_where=text("published_at IS NULL"),
        ),
    )

    aggregate_type: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[uuid.UUID]
    event_type: Mapped[str] = mapped_column(String(128))

    # Маршрутизация лежит в строке, поэтому relay ничего не знает про типы событий.
    exchange: Mapped[str] = mapped_column(String(255))
    routing_key: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict[str, Any]]

    published_at: Mapped[datetime | None] = mapped_column(default=None)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text, default=None)

    def __repr__(self) -> str:
        published = "published" if self.published_at else "pending"
        return f"<OutboxMessage id={self.id} {self.event_type} {published}>"
