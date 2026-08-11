from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from payment_service.api.app import create_app, lifespan
from payment_service.config import Settings
from payment_service.db import SessionFactory
from payment_service.enums import PaymentStatus
from payment_service.models import OutboxMessage, Payment

pytestmark = pytest.mark.integration

BODY: dict[str, Any] = {
    "amount": "100.50",
    "currency": "RUB",
    "description": "Заказ 42",
    "metadata": {"order_id": "42", "source": "web"},
    "webhook_url": "http://webhook-sink:8080/hook",
}


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    # ASGITransport lifespan не запускает, а именно там собирается session_factory,
    # так что стартовая обвязка приложения проверяется вместе с ручками.
    async with (
        lifespan(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        yield client


def headers(key: str = "idem-1") -> dict[str, str]:
    return {"X-API-Key": "test-api-key", "Idempotency-Key": key}


async def test_health_needs_no_key(client: httpx.AsyncClient) -> None:
    """Оркестратор не должен знать секрет, чтобы понять, жив ли контейнер."""
    response = await client.get("/health")

    assert response.status_code == 200


@pytest.mark.parametrize("key", [None, "wrong-key"])
async def test_missing_or_wrong_key_is_rejected(client: httpx.AsyncClient, key: str | None) -> None:
    sent = {"Idempotency-Key": "x"} | ({"X-API-Key": key} if key else {})

    response = await client.post("/api/v1/payments", json=BODY, headers=sent)

    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


async def test_create_returns_accepted(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/payments", json=BODY, headers=headers())

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == PaymentStatus.PENDING
    assert body["payment_id"]
    assert response.headers["Idempotent-Replay"] == "false"


async def test_payment_and_event_are_written_together(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    response = await client.post("/api/v1/payments", json=BODY, headers=headers())
    payment_id = response.json()["payment_id"]

    async with session_factory() as session:
        payment = await session.get(Payment, payment_id)
        event = await session.scalar(
            select(OutboxMessage).where(OutboxMessage.aggregate_id == payment_id)
        )

    assert payment is not None
    assert payment.status is PaymentStatus.PENDING
    assert payment.meta == BODY["metadata"]
    assert event is not None
    assert event.published_at is None


async def test_repeated_key_returns_the_same_payment(
    client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    first = await client.post("/api/v1/payments", json=BODY, headers=headers())
    # Поля переставлены: отпечаток канонизируется, значит это тот же запрос.
    reordered = {"currency": "RUB", "amount": "100.50", **BODY}
    second = await client.post("/api/v1/payments", json=reordered, headers=headers())

    assert second.status_code == 202
    assert second.json()["payment_id"] == first.json()["payment_id"]
    assert second.headers["Idempotent-Replay"] == "true"

    async with session_factory() as session:
        payments = await session.scalar(select(func.count()).select_from(Payment))
        events = await session.scalar(select(func.count()).select_from(OutboxMessage))

    assert payments == 1
    # Повтор не должен порождать второе событие.
    assert events == 1


async def test_same_key_with_another_body_conflicts(client: httpx.AsyncClient) -> None:
    await client.post("/api/v1/payments", json=BODY, headers=headers())

    response = await client.post(
        "/api/v1/payments", json=BODY | {"amount": "999.00"}, headers=headers()
    )

    assert response.status_code == 409
    assert response.json()["code"] == "idempotency_conflict"


async def test_get_returns_details(client: httpx.AsyncClient) -> None:
    created = await client.post("/api/v1/payments", json=BODY, headers=headers())
    payment_id = created.json()["payment_id"]

    response = await client.get(f"/api/v1/payments/{payment_id}", headers=headers())

    body = response.json()
    assert response.status_code == 200
    assert body["payment_id"] == payment_id
    assert body["amount"] == "100.50"
    assert body["metadata"] == BODY["metadata"]
    assert body["processed_at"] is None


async def test_unknown_payment_is_not_found(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/api/v1/payments/00000000-0000-0000-0000-000000000000", headers=headers()
    )

    assert response.status_code == 404
    assert response.json()["code"] == "payment_not_found"


@pytest.mark.parametrize(
    "patch",
    [
        {"amount": "-1"},
        {"amount": "0"},
        {"currency": "GBP"},
        {"description": ""},
        {"webhook_url": "не-ссылка"},
        {"unexpected": "field"},
    ],
)
async def test_invalid_body_is_rejected(client: httpx.AsyncClient, patch: dict[str, Any]) -> None:
    response = await client.post("/api/v1/payments", json=BODY | patch, headers=headers())

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_unexpected_error_does_not_leak_internals(settings: Settings) -> None:
    """Текст исключения наружу не отдаём: в нём бывают строки подключения и ключи."""
    app = create_app(settings)

    @app.get("/boom")
    async def boom() -> None:
        msg = "postgresql://user:s3cret@db:5432/payments недоступна"
        raise RuntimeError(msg)

    # Иначе httpx перевыбросит исключение вместо ответа, который вернул обработчик.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "s3cret" not in response.text


async def test_idempotency_key_is_required(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/payments", json=BODY, headers={"X-API-Key": "test-api-key"}
    )

    assert response.status_code == 422
