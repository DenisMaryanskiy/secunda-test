import uuid


class PaymentServiceError(Exception):
    """Базовая ошибка домена. Наружу отдаётся кодом, а не текстом исключения."""


class PaymentNotFoundError(PaymentServiceError):
    def __init__(self, payment_id: uuid.UUID) -> None:
        self.payment_id = payment_id
        super().__init__(f"Платёж {payment_id} не найден")


class IdempotencyConflictError(PaymentServiceError):
    def __init__(self, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(f"Ключ идемпотентности {idempotency_key} уже использован с другим телом")


class EventNotRoutedError(PaymentServiceError):
    """Брокер принял сообщение, но не нашёл очереди, куда его положить."""

    def __init__(self, exchange: str, routing_key: str) -> None:
        super().__init__(f"Событие некуда маршрутизировать: {exchange} -> {routing_key}")


class UnsafeWebhookUrlError(PaymentServiceError):
    """Адрес webhook ведёт во внутреннюю сеть или использует запрещённую схему."""
