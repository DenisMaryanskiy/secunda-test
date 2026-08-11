import asyncio
import hashlib
import hmac
import json
from typing import Protocol

import httpx
import structlog

from payment_service.config import WebhookSettings
from payment_service.errors import WebhookDeliveryError
from payment_service.models import Payment
from payment_service.models.base import utcnow
from payment_service.webhook_url import ensure_safe_webhook_target

log = structlog.get_logger(__name__)

SIGNATURE_HEADER = "X-Payment-Signature"
TIMESTAMP_HEADER = "X-Payment-Timestamp"


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """
    Подпись webhook, совместимая по схеме со Stripe и GitHub.

    Timestamp входит в подписываемые данные, иначе перехваченный запрос можно
    было бы бесконечно переигрывать: получатель проверяет и подпись, и свежесть.
    """
    payload = f"{timestamp}.".encode() + body
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def build_payload(payment: Payment) -> dict[str, object]:
    return {
        "payment_id": str(payment.id),
        "status": payment.status.value,
        "amount": str(payment.amount),
        "currency": payment.currency.value,
        "failure_reason": payment.failure_reason,
        "processed_at": payment.processed_at.isoformat() if payment.processed_at else None,
    }


class WebhookNotifier(Protocol):
    async def notify(self, payment: Payment) -> None: ...


class HttpWebhookNotifier:
    def __init__(self, client: httpx.AsyncClient, settings: WebhookSettings) -> None:
        self._client = client
        self._settings = settings

    async def notify(self, payment: Payment) -> None:
        body = json.dumps(build_payload(payment), separators=(",", ":")).encode()
        timestamp = int(utcnow().timestamp())
        headers = {
            "Content-Type": "application/json",
            TIMESTAMP_HEADER: str(timestamp),
            SIGNATURE_HEADER: sign(
                self._settings.signing_secret.get_secret_value(), timestamp, body
            ),
        }

        last_error = ""
        for attempt in range(1, self._settings.max_attempts + 1):
            try:
                await self._post(payment.webhook_url, body, headers)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning(
                    "webhook.attempt_failed",
                    payment_id=str(payment.id),
                    attempt=attempt,
                    error=last_error,
                )
                if attempt < self._settings.max_attempts:
                    delay = self._settings.retry_base_delay_seconds * 2 ** (attempt - 1)
                    await asyncio.sleep(delay)
            else:
                log.info("webhook.delivered", payment_id=str(payment.id), attempt=attempt)
                return

        raise WebhookDeliveryError(self._settings.max_attempts, last_error)

    async def _post(self, url: str, body: bytes, headers: dict[str, str]) -> None:
        await ensure_safe_webhook_target(url, allow_insecure=self._settings.allow_insecure_targets)
        # stream вместо post: тело ответа нам не нужно, а читать в память
        # неизвестно сколько байт от чужого сервера незачем.
        async with self._client.stream("POST", url, content=body, headers=headers) as response:
            response.raise_for_status()


def create_webhook_client(settings: WebhookSettings) -> httpx.AsyncClient:
    # follow_redirects=False обязателен: иначе SSRF-проверку обходит редирект
    # с безопасного адреса на внутренний.
    return httpx.AsyncClient(timeout=settings.timeout_seconds, follow_redirects=False)
