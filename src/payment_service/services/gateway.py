import asyncio
import random
import uuid
from dataclasses import dataclass
from typing import Protocol

import structlog

from payment_service.config import GatewaySettings

log = structlog.get_logger(__name__)

FAILURE_REASONS = (
    "insufficient_funds",
    "card_declined",
    "gateway_timeout",
)


@dataclass(frozen=True, slots=True)
class ChargeResult:
    succeeded: bool
    failure_reason: str | None = None


class PaymentGateway(Protocol):
    async def charge(self, payment_id: uuid.UUID) -> ChargeResult: ...


class EmulatedPaymentGateway:
    def __init__(self, settings: GatewaySettings) -> None:
        self._settings = settings

    async def charge(self, payment_id: uuid.UUID) -> ChargeResult:
        delay = random.uniform(  # noqa: S311
            self._settings.min_delay_seconds,
            self._settings.max_delay_seconds,
        )
        await asyncio.sleep(delay)

        if random.random() < self._settings.success_rate:  # noqa: S311
            log.info("gateway.succeeded", payment_id=str(payment_id), delay=round(delay, 2))
            return ChargeResult(succeeded=True)

        reason = random.choice(FAILURE_REASONS)  # noqa: S311
        log.info("gateway.failed", payment_id=str(payment_id), reason=reason)
        return ChargeResult(succeeded=False, failure_reason=reason)
