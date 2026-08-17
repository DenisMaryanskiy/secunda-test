import hashlib
import hmac
import json
from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest

from payment_service.adapters.webhooks import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    HttpWebhookNotifier,
    build_payload,
    create_webhook_client,
    sign,
)
from payment_service.config import WebhookSettings
from payment_service.enums import PaymentStatus
from payment_service.errors import UnsafeWebhookUrlError, WebhookDeliveryError
from payment_service.models.base import utcnow
from tests.factories import make_payment

SECRET = "test-signing-secret"


@pytest.fixture
def webhook_settings() -> WebhookSettings:
    return WebhookSettings(
        signing_secret=SECRET,
        max_attempts=3,
        retry_base_delay_seconds=0.001,
        allow_insecure_targets=True,
    )


type Handler = Callable[[httpx.Request], httpx.Response]


def notifier_for(settings: WebhookSettings, handler: Handler) -> HttpWebhookNotifier:
    transport = httpx.MockTransport(handler)
    return HttpWebhookNotifier(httpx.AsyncClient(transport=transport), settings)


def test_signature_matches_independent_computation() -> None:
    body = b'{"payment_id":"x"}'

    expected = hmac.new(SECRET.encode(), b"1786467244." + body, hashlib.sha256).hexdigest()

    assert sign(SECRET, 1786467244, body) == f"sha256={expected}"


def test_signature_depends_on_timestamp() -> None:
    """Без timestamp в подписи перехваченный запрос можно было бы переигрывать вечно."""
    body = b"{}"

    assert sign(SECRET, 1, body) != sign(SECRET, 2, body)


def test_signature_depends_on_body() -> None:
    assert sign(SECRET, 1, b'{"a":1}') != sign(SECRET, 1, b'{"a":2}')


def test_payload_carries_outcome() -> None:
    payment = make_payment(
        status=PaymentStatus.FAILED,
        failure_reason="card_declined",
        processed_at=utcnow(),
        amount=Decimal("12.30"),
    )

    payload = build_payload(payment)

    assert payload["payment_id"] == str(payment.id)
    assert payload["status"] == "failed"
    assert payload["failure_reason"] == "card_declined"
    assert payload["amount"] == "12.30"
    assert payload["processed_at"] is not None


async def test_delivers_signed_request(webhook_settings: WebhookSettings) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    await notifier_for(webhook_settings, handler).notify(make_payment())

    request = seen[0]
    body = request.read()
    timestamp = int(request.headers[TIMESTAMP_HEADER])
    assert request.headers[SIGNATURE_HEADER] == sign(SECRET, timestamp, body)
    # Подписаны ровно те байты, что ушли в сеть.
    assert json.loads(body)["payment_id"]


async def test_retries_until_success(webhook_settings: WebhookSettings) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200 if attempts == 3 else 500)

    await notifier_for(webhook_settings, handler).notify(make_payment())

    assert attempts == 3


async def test_gives_up_after_max_attempts(webhook_settings: WebhookSettings) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503)

    with pytest.raises(WebhookDeliveryError, match="3 попыток"):
        await notifier_for(webhook_settings, handler).notify(make_payment())

    assert attempts == webhook_settings.max_attempts


async def test_unsafe_target_blocks_request(webhook_settings: WebhookSettings) -> None:
    """SSRF-проверка должна отработать до того, как запрос уйдёт."""
    strict = webhook_settings.model_copy(update={"allow_insecure_targets": False})
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    payment = make_payment(webhook_url="https://169.254.169.254/latest/meta-data/")
    with pytest.raises(WebhookDeliveryError) as exc_info:
        await notifier_for(strict, handler).notify(payment)

    assert not called
    assert UnsafeWebhookUrlError.__name__ in str(exc_info.value)


def test_client_does_not_follow_redirects(webhook_settings: WebhookSettings) -> None:
    """Редирект с безопасного адреса на внутренний обходил бы SSRF-проверку."""
    client = create_webhook_client(webhook_settings)

    assert client.follow_redirects is False
    assert client.timeout.connect == webhook_settings.timeout_seconds
