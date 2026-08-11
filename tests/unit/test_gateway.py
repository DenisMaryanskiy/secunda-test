import random
import uuid

import pytest

from payment_service.config import GatewaySettings
from payment_service.services.gateway import FAILURE_REASONS, EmulatedPaymentGateway


@pytest.fixture
def instant() -> GatewaySettings:
    """Без пауз: тесты не должны ждать эмулируемый шлюз."""
    return GatewaySettings(min_delay_seconds=0, max_delay_seconds=0)


async def test_always_succeeds_at_full_success_rate(instant: GatewaySettings) -> None:
    gateway = EmulatedPaymentGateway(instant.model_copy(update={"success_rate": 1.0}))

    result = await gateway.charge(uuid.uuid4())

    assert result.succeeded
    assert result.failure_reason is None


async def test_always_fails_at_zero_success_rate(instant: GatewaySettings) -> None:
    gateway = EmulatedPaymentGateway(instant.model_copy(update={"success_rate": 0.0}))

    result = await gateway.charge(uuid.uuid4())

    assert not result.succeeded
    assert result.failure_reason in FAILURE_REASONS


async def test_success_rate_is_respected(instant: GatewaySettings) -> None:
    """Не строгое равенство: проверяем, что доля отказов близка к заданной."""
    # Состояние возвращаем на место: seed глобальный, а тесты не должны влиять друг на друга.
    state = random.getstate()
    random.seed(20260811)
    gateway = EmulatedPaymentGateway(instant.model_copy(update={"success_rate": 0.9}))

    try:
        outcomes = [(await gateway.charge(uuid.uuid4())).succeeded for _ in range(1000)]
    finally:
        random.setstate(state)

    assert 0.86 <= sum(outcomes) / len(outcomes) <= 0.94


async def test_delay_stays_within_configured_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def capture(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr("payment_service.services.gateway.asyncio.sleep", capture)
    settings = GatewaySettings(min_delay_seconds=2, max_delay_seconds=5, success_rate=1.0)
    gateway = EmulatedPaymentGateway(settings)

    for _ in range(50):
        await gateway.charge(uuid.uuid4())

    assert all(2 <= delay <= 5 for delay in slept)
    # Разброс есть, а не одно и то же число.
    assert len(set(slept)) > 1
