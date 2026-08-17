from typing import Annotated

from fastapi import Header

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
        description="Уникальный ключ запроса. Повтор с тем же ключом не создаёт новый платёж.",
    ),
]
