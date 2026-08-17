import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Self

from pydantic import ConfigDict, Field

from payment_service.enums import Currency, PaymentStatus
from payment_service.models import Payment
from payment_service.schemas.base import BaseRequest, BaseResponse
from payment_service.schemas.common import Amount, Metadata, WebhookUrl


class PaymentCreateRequest(BaseRequest):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "amount": "100.50",
                    "currency": "RUB",
                    "description": "Заказ 42",
                    "metadata": {"order_id": "42", "source": "web"},
                    "webhook_url": "https://shop.example.com/payments/hook",
                }
            ]
        }
    )

    amount: Amount
    currency: Currency
    description: str = Field(min_length=1, max_length=512)
    metadata: Metadata = Field(default_factory=dict)
    webhook_url: WebhookUrl


class PaymentAcceptedResponse(BaseResponse):
    """Ответ на создание: платёж только принят, результата обработки ещё нет."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "payment_id": "88fff74e-6f1a-4d5b-9c2e-7a1b3c4d5e6f",
                    "status": "pending",
                    "created_at": "2026-08-11T20:10:01.104512+00:00",
                }
            ]
        }
    )

    payment_id: uuid.UUID
    status: PaymentStatus
    created_at: datetime

    @classmethod
    def from_model(cls, payment: Payment) -> Self:
        return cls(payment_id=payment.id, status=payment.status, created_at=payment.created_at)


class PaymentResponse(BaseResponse):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "payment_id": "88fff74e-6f1a-4d5b-9c2e-7a1b3c4d5e6f",
                    "amount": "100.50",
                    "currency": "RUB",
                    "description": "Заказ 42",
                    "metadata": {"order_id": "42", "source": "web"},
                    "status": "succeeded",
                    "failure_reason": None,
                    "webhook_url": "https://shop.example.com/payments/hook",
                    "created_at": "2026-08-11T20:10:01.104512+00:00",
                    "processed_at": "2026-08-11T20:10:06.316084+00:00",
                    "webhook_delivered_at": "2026-08-11T20:10:06.482301+00:00",
                }
            ]
        }
    )

    payment_id: uuid.UUID
    amount: Decimal
    currency: Currency
    description: str
    metadata: dict[str, Any]
    status: PaymentStatus
    failure_reason: str | None
    webhook_url: str
    created_at: datetime
    processed_at: datetime | None
    webhook_delivered_at: datetime | None

    @classmethod
    def from_model(cls, payment: Payment) -> Self:
        return cls(
            payment_id=payment.id,
            amount=payment.amount,
            currency=payment.currency,
            description=payment.description,
            metadata=payment.meta,
            status=payment.status,
            failure_reason=payment.failure_reason,
            webhook_url=payment.webhook_url,
            created_at=payment.created_at,
            processed_at=payment.processed_at,
            webhook_delivered_at=payment.webhook_delivered_at,
        )
