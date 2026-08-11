import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, UrlConstraints

from payment_service.enums import Currency, PaymentStatus
from payment_service.models import Payment

# Границы те же, что у колонок в БД: NUMERIC(18, 2) и VARCHAR(2048).
Amount = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]
WebhookUrl = Annotated[HttpUrl, UrlConstraints(max_length=2048)]


class PaymentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Amount
    currency: Currency
    description: str = Field(min_length=1, max_length=512)
    metadata: dict[str, Any] = Field(default_factory=dict)
    webhook_url: WebhookUrl


class PaymentAcceptedResponse(BaseModel):
    """Ответ на создание: платёж только принят, результата обработки ещё нет."""

    payment_id: uuid.UUID
    status: PaymentStatus
    created_at: datetime

    @classmethod
    def from_model(cls, payment: Payment) -> Self:
        return cls(payment_id=payment.id, status=payment.status, created_at=payment.created_at)


class PaymentResponse(BaseModel):
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


class ErrorResponse(BaseModel):
    code: str
    message: str
