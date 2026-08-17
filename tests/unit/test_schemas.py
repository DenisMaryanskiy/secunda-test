import pytest
from pydantic import ValidationError

from payment_service.schemas.common import MAX_METADATA_BYTES
from payment_service.schemas.error import ErrorResponse
from payment_service.schemas.payment import PaymentCreateRequest

BODY = {
    "amount": "100.50",
    "currency": "RUB",
    "description": "Заказ 42",
    "webhook_url": "https://shop.example.com/hook",
}


def test_metadata_within_limit_is_accepted() -> None:
    request = PaymentCreateRequest(**BODY, metadata={"order_id": "42"})

    assert request.metadata == {"order_id": "42"}


def test_oversized_metadata_is_rejected() -> None:
    """У jsonb нет ограничения длины, поэтому размер ловится только схемой."""
    oversized = {"blob": "x" * (MAX_METADATA_BYTES + 1)}

    with pytest.raises(ValidationError, match="metadata занимает"):
        PaymentCreateRequest(**BODY, metadata=oversized)


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PaymentCreateRequest(**BODY, unexpected="field")  # type: ignore[call-arg]


def test_response_is_frozen() -> None:
    response = ErrorResponse(code="payment_not_found", message="Платёж не найден")

    with pytest.raises(ValidationError, match="frozen"):
        response.code = "other"  # type: ignore[misc]
