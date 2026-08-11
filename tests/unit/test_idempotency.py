from decimal import Decimal

import pytest

from payment_service.enums import Currency
from payment_service.services.commands import NewPayment
from payment_service.services.idempotency import request_fingerprint


def make(**overrides: object) -> NewPayment:
    payload: dict[str, object] = {
        "amount": Decimal("100.50"),
        "currency": Currency.RUB,
        "description": "Заказ 42",
        "metadata": {"order_id": "42", "source": "web"},
        "webhook_url": "https://example.com/hook",
    }
    return NewPayment(**(payload | overrides))


def test_same_request_gives_same_fingerprint() -> None:
    assert request_fingerprint(make()) == request_fingerprint(make())


def test_metadata_key_order_does_not_matter() -> None:
    """Иначе честный повтор запроса прилетел бы клиенту как 409."""
    reordered = make(metadata={"source": "web", "order_id": "42"})

    assert request_fingerprint(make()) == request_fingerprint(reordered)


@pytest.mark.parametrize(
    "field, value",
    [
        ("amount", Decimal("100.51")),
        ("currency", Currency.USD),
        ("description", "Заказ 43"),
        ("metadata", {"order_id": "43"}),
        ("webhook_url", "https://example.com/other"),
    ],
)
def test_any_changed_field_changes_fingerprint(field: str, value: object) -> None:
    assert request_fingerprint(make()) != request_fingerprint(make(**{field: value}))


def test_trailing_zeros_in_amount_matter() -> None:
    """100.5 и 100.50 - разные строки в JSON, значит и разные отпечатки."""
    assert request_fingerprint(make(amount=Decimal("100.5"))) != request_fingerprint(make())
