from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from payment_service.enums import Currency


class NewPayment(BaseModel):
    """
    Данные платежа на входе в сервис, уже провалидированные транспортом.

    Ключ идемпотентности сюда не входит намеренно: отпечаток считается по всей
    модели, а с ключом внутри он был бы уникален всегда и конфликт не ловился бы.
    """

    model_config = ConfigDict(frozen=True)

    amount: Decimal
    currency: Currency
    description: str
    metadata: dict[str, Any]
    webhook_url: str
