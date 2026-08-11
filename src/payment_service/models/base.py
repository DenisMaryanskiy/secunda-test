import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Enum, MetaData, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Явные имена констрейнтов, иначе Alembic генерирует безымянные и их нельзя откатить.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {  # noqa: RUF012
        dict[str, Any]: JSONB,
        uuid.UUID: UUID(as_uuid=True),
        datetime: DateTime(timezone=True),
    }


def pg_enum[E: enum.Enum](enum_type: type[E], name: str) -> Enum:
    """
    Enum, который хранит в БД значения, а не имена питоновских констант.

    По умолчанию SQLAlchemy пишет туда name (PENDING), из-за чего лейблы типа
    расходятся с тем, что видит API, и ломаются предикаты вроде status = 'pending'.
    """
    return Enum(enum_type, name=name, values_callable=lambda e: [member.value for member in e])


# sort_order держит id и created_at в начале таблицы, а не в хвосте после полей модели.
class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, sort_order=-100)


def utcnow() -> datetime:
    return datetime.now(UTC)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        default=utcnow,
        sort_order=-99,
    )
