import pytest

from payment_service.errors import UnsafeWebhookUrlError
from payment_service.webhook_url import ensure_safe_webhook_target


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/hook",
        "ftp://example.com/hook",
        "file:///etc/passwd",
        "gopher://example.com/",
    ],
)
async def test_only_https_allowed(url: str) -> None:
    with pytest.raises(UnsafeWebhookUrlError, match="Схема"):
        await ensure_safe_webhook_target(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/hook",
        "https://localhost/hook",
        "https://10.0.0.1/hook",
        "https://192.168.1.1/hook",
        "https://172.16.0.1/hook",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::1]/hook",
    ],
)
async def test_internal_addresses_rejected(url: str) -> None:
    with pytest.raises(UnsafeWebhookUrlError, match="внутреннюю сеть"):
        await ensure_safe_webhook_target(url)


async def test_public_address_allowed() -> None:
    await ensure_safe_webhook_target("https://93.184.216.34/hook")


async def test_url_without_host_rejected() -> None:
    with pytest.raises(UnsafeWebhookUrlError, match="хост"):
        await ensure_safe_webhook_target("https:///hook")


async def test_insecure_mode_skips_checks() -> None:
    """Локальная разработка: webhook уезжает в соседний контейнер по http."""
    await ensure_safe_webhook_target("http://webhook-sink:8080/hook", allow_insecure=True)


async def test_insecure_mode_still_requires_known_scheme() -> None:
    with pytest.raises(UnsafeWebhookUrlError, match="Схема"):
        await ensure_safe_webhook_target("ftp://webhook-sink/hook", allow_insecure=True)


async def test_unresolvable_host_is_rejected() -> None:
    """Ошибку DNS нельзя молча пропускать: непроверенный адрес - непроверенный адрес."""
    with pytest.raises(UnsafeWebhookUrlError, match="разрезолвить"):
        await ensure_safe_webhook_target("https://такого-хоста-нет.invalid/hook")
