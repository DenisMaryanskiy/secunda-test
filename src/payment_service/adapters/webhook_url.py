"""
Защита от SSRF: webhook_url приходит от клиента, а ходит по нему наш консьюмер
изнутри периметра. Без проверки это способ заставить сервис постучаться
в соседний контейнер или в 169.254.169.254 за облачными кредами.

Проверка одна и живёт там, где происходит сам запрос. Заранее, при создании
платежа, её делать бессмысленно: между валидацией и отправкой webhook проходят
секунды, и запись в DNS за это время может смениться на приватный адрес.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from payment_service.errors import UnsafeWebhookUrlError

SECURE_SCHEMES = frozenset({"https"})
INSECURE_SCHEMES = frozenset({"http", "https"})

RESOLVE_TIMEOUT_SECONDS = 5.0


async def ensure_safe_webhook_target(url: str, *, allow_insecure: bool = False) -> None:
    """
    Проверяет схему и резолвит хост, отбраковывая адреса внутренней сети.
    """
    parts = urlsplit(url)

    allowed = INSECURE_SCHEMES if allow_insecure else SECURE_SCHEMES
    if parts.scheme not in allowed:
        msg = f"Схема {parts.scheme!r} запрещена, ожидается одна из {sorted(allowed)}"
        raise UnsafeWebhookUrlError(msg)

    host = parts.hostname
    if not host:
        msg = "В webhook_url не указан хост"
        raise UnsafeWebhookUrlError(msg)

    if allow_insecure:
        return

    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, type=socket.SOCK_STREAM),
            RESOLVE_TIMEOUT_SECONDS,
        )
    except (socket.gaierror, TimeoutError) as exc:
        msg = f"Не удалось разрезолвить хост {host}"
        raise UnsafeWebhookUrlError(msg) from exc

    for info in infos:
        address = ipaddress.ip_address(str(info[4][0]))
        # is_global ложен для loopback, private, link-local, multicast и reserved -
        # то есть ровно для всего, куда клиентский webhook ходить не должен.
        if not address.is_global:
            msg = f"Адрес {address} хоста {host} ведёт во внутреннюю сеть"
            raise UnsafeWebhookUrlError(msg)
