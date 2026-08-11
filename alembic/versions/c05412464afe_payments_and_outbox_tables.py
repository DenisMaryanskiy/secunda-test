"""payments and outbox tables

Revision ID: c05412464afe
Revises:
Create Date: 2026-08-11 17:15:32.725523

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c05412464afe"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("exchange", sa.String(length=255), nullable=False),
        sa.Column("routing_key", sa.String(length=255), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_messages")),
    )
    op.create_index(
        "ix_outbox_messages_unpublished",
        "outbox_messages",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.create_table(
        "payments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.Enum("RUB", "USD", "EUR", name="currency"), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("pending", "succeeded", "failed", name="payment_status"),
            server_default=sa.text("'pending'::payment_status"),
            nullable=False,
        ),
        sa.Column("failure_reason", sa.String(length=255), nullable=True),
        sa.Column("webhook_url", sa.String(length=2048), nullable=False),
        sa.Column("webhook_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount > 0", name=op.f("ck_payments_amount_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_payments_idempotency_key")),
    )
    op.create_index(
        "ix_payments_pending_created_at",
        "payments",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_payments_pending_created_at",
        table_name="payments",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_table("payments")
    op.drop_index(
        "ix_outbox_messages_unpublished",
        table_name="outbox_messages",
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.drop_table("outbox_messages")
    # drop_table не удаляет типы, созданные под колонки. Здесь дропаем типы целиком
    # (не отдельные лейблы - такого в Postgres нет), иначе повторный upgrade
    # падает на "type payment_status already exists".
    for enum_name in ("payment_status", "currency"):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
