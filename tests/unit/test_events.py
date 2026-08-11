from decimal import Decimal

from payment_service.enums import Currency, PaymentStatus
from payment_service.messaging.events import (
    PAYMENT_CREATED,
    PaymentCreatedEvent,
    payment_created_message,
)
from payment_service.messaging.topology import PAYMENT_CREATED_ROUTING_KEY, PAYMENTS_EXCHANGE
from tests.factories import make_payment


def test_message_carries_routing_and_aggregate() -> None:
    payment = make_payment()

    message = payment_created_message(payment)

    assert message.event_type == PAYMENT_CREATED
    assert message.exchange == PAYMENTS_EXCHANGE
    assert message.routing_key == PAYMENT_CREATED_ROUTING_KEY
    assert message.aggregate_type == "payment"
    assert message.aggregate_id == payment.id
    assert message.published_at is None


def test_event_id_matches_the_outbox_row() -> None:
    """id записи уезжает в message_id, поэтому дубликат публикации видно в брокере."""
    message = payment_created_message(make_payment())

    assert message.payload["event_id"] == str(message.id)


def test_payload_is_json_ready() -> None:
    payment = make_payment(amount=Decimal("100.50"), currency=Currency.EUR)

    payload = payment_created_message(payment).payload

    # JSONB не умеет в Decimal и UUID, поэтому всё уже приведено к строкам.
    assert payload["amount"] == "100.50"
    assert payload["currency"] == "EUR"
    assert payload["payment_id"] == str(payment.id)
    assert PaymentCreatedEvent.model_validate(payload).payment_id == payment.id


def test_terminal_statuses() -> None:
    assert not PaymentStatus.PENDING.is_terminal
    assert PaymentStatus.SUCCEEDED.is_terminal
    assert PaymentStatus.FAILED.is_terminal


def test_repr_is_readable_in_logs() -> None:
    payment = make_payment()
    message = payment_created_message(payment)

    assert str(payment.id) in repr(payment)
    assert "pending" in repr(message)
