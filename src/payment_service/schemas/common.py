import json
from decimal import Decimal
from typing import Annotated, Any

from pydantic import AfterValidator, Field, HttpUrl, UrlConstraints

# Границы те же, что у колонок в БД: NUMERIC(18, 2) и VARCHAR(2048).
Amount = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]
WebhookUrl = Annotated[HttpUrl, UrlConstraints(max_length=2048)]

MAX_METADATA_BYTES = 8 * 1024


def _within_size_limit(value: dict[str, Any]) -> dict[str, Any]:
    # У jsonb-колонки нет ограничения длины, как у varchar, поэтому размер
    # приходится проверять здесь: иначе клиент запишет мегабайт на платёж.
    size = len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())
    if size > MAX_METADATA_BYTES:
        msg = f"metadata занимает {size} байт, допустимо не больше {MAX_METADATA_BYTES}"
        raise ValueError(msg)
    return value


Metadata = Annotated[dict[str, Any], AfterValidator(_within_size_limit)]
