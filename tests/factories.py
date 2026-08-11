import uuid
from decimal import Decimal
from typing import Any

from payment_service.enums import Currency, PaymentStatus
from payment_service.models import Payment
from payment_service.models.base import utcnow


def make_payment(**overrides: Any) -> Payment:
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "created_at": utcnow(),
        "idempotency_key": f"key-{uuid.uuid4()}",
        "request_fingerprint": "0" * 64,
        "amount": Decimal("100.50"),
        "currency": Currency.RUB,
        "description": "Заказ 42",
        "meta": {"order_id": "42"},
        "status": PaymentStatus.PENDING,
        "webhook_url": "https://example.com/hook",
    }
    return Payment(**(fields | overrides))
