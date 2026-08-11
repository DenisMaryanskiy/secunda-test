import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from payment_service.enums import Currency
from payment_service.messaging.topology import PAYMENT_CREATED_ROUTING_KEY, PAYMENTS_EXCHANGE
from payment_service.models import OutboxMessage, Payment

PAYMENT_CREATED = "payment.created"


class PaymentCreatedEvent(BaseModel):
    """
    Контракт сообщения в очереди payments.new.

    Сумма и валюта дублируют строку в БД намеренно: с ними сообщение читаемо
    в UI RabbitMQ и в DLQ, когда до базы ещё нужно дойти. Актуальный статус
    консьюмер всё равно перечитывает из БД.
    """

    event_id: uuid.UUID
    payment_id: uuid.UUID
    amount: Decimal
    currency: Currency
    occurred_at: datetime


def payment_created_message(payment: Payment) -> OutboxMessage:
    event_id = uuid.uuid4()
    event = PaymentCreatedEvent(
        event_id=event_id,
        payment_id=payment.id,
        amount=payment.amount,
        currency=payment.currency,
        occurred_at=payment.created_at,
    )
    return OutboxMessage(
        id=event_id,
        aggregate_type="payment",
        aggregate_id=payment.id,
        event_type=PAYMENT_CREATED,
        exchange=PAYMENTS_EXCHANGE,
        routing_key=PAYMENT_CREATED_ROUTING_KEY,
        payload=event.model_dump(mode="json"),
    )
