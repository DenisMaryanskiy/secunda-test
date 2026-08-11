from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, Index, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from payment_service.enums import Currency, PaymentStatus
from payment_service.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin, pg_enum


class Payment(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        # Индекс под выборку зависших платежей: "кто в pending дольше N минут".
        Index(
            "ix_payments_pending_created_at",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    # sha256 нормализованного тела запроса: ловит переиспользование ключа с другим телом.
    request_fingerprint: Mapped[str] = mapped_column(String(64))

    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[Currency] = mapped_column(pg_enum(Currency, "currency"))
    description: Mapped[str] = mapped_column(String(512))
    # metadata занято SQLAlchemy, поэтому атрибут называется иначе, чем колонка.
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", default=dict, server_default=text("'{}'::jsonb")
    )

    status: Mapped[PaymentStatus] = mapped_column(
        pg_enum(PaymentStatus, "payment_status"),
        default=PaymentStatus.PENDING,
        server_default=text(f"'{PaymentStatus.PENDING.value}'::payment_status"),
    )
    failure_reason: Mapped[str | None] = mapped_column(String(255), default=None)

    webhook_url: Mapped[str] = mapped_column(String(2048))
    webhook_delivered_at: Mapped[datetime | None] = mapped_column(default=None)

    processed_at: Mapped[datetime | None] = mapped_column(default=None)

    def __repr__(self) -> str:
        return f"<Payment id={self.id} status={self.status} amount={self.amount} {self.currency}>"
