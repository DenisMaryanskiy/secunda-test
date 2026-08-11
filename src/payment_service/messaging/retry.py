from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from faststream import BaseMiddleware
from faststream.message import StreamMessage
from faststream.rabbit import RabbitBroker

from payment_service.config import ConsumerSettings
from payment_service.messaging.topology import (
    ATTEMPT_HEADER,
    DLX_EXCHANGE,
    PAYMENT_CREATED_ROUTING_KEY,
    RETRY_EXCHANGE,
    retry_routing_key,
)

log = structlog.get_logger(__name__)

ERROR_HEADER = "x-last-error"
MAX_ERROR_HEADER_LENGTH = 500


class RetryMiddleware(BaseMiddleware[Any, Any]):
    """
    Повторы и DLQ. Обработчик о них не знает и просто бросает исключение.

    Упавшее сообщение перекладывается в retry-очередь со счётчиком попыток
    в заголовке, а после исчерпания попыток - в DLX. Исходное сообщение при этом
    подтверждается: за доставку дальше отвечает уже новая копия.
    """

    def __init__(
        self,
        msg: Any,
        *,
        context: Any,
        broker: RabbitBroker,
        settings: ConsumerSettings,
    ) -> None:
        super().__init__(msg, context=context)
        self._broker = broker
        self._settings = settings

    async def consume_scope(
        self,
        call_next: Callable[[Any], Awaitable[Any]],
        msg: StreamMessage[Any],
    ) -> Any:
        try:
            return await call_next(msg)
        except Exception as exc:
            await self._reschedule(msg, exc)
            # Исключение дальше не пускаем: иначе FastStream отвергнет сообщение,
            # и оно уедет в DLQ по DLX очереди, минуя счётчик попыток.
            return None

    async def _reschedule(self, msg: StreamMessage[Any], exc: Exception) -> None:
        attempt = int(msg.headers.get(ATTEMPT_HEADER, 1))
        error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_HEADER_LENGTH]
        context = {
            "message_id": msg.message_id,
            "attempt": attempt,
            "max_attempts": self._settings.max_attempts,
            "error": error,
        }

        if attempt >= self._settings.max_attempts:
            log.error("consumer.dead_lettered", **context)
            await self._broker.publish(
                msg.body,
                exchange=DLX_EXCHANGE,
                routing_key=PAYMENT_CREATED_ROUTING_KEY,
                persist=True,
                message_id=msg.message_id,
                headers={ATTEMPT_HEADER: attempt, ERROR_HEADER: error},
            )
            return

        log.warning("consumer.retry_scheduled", **context)
        await self._broker.publish(
            msg.body,
            exchange=RETRY_EXCHANGE,
            routing_key=retry_routing_key(attempt),
            persist=True,
            # Тот же id, что проставил relay: иначе на повторной доставке
            # теряется сквозной идентификатор события.
            message_id=msg.message_id,
            headers={ATTEMPT_HEADER: attempt + 1, ERROR_HEADER: error},
        )
