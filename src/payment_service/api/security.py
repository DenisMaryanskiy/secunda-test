import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from payment_service.api.deps import SettingsDep

API_KEY_HEADER = "X-API-Key"

# auto_error=False: свой обработчик отдаёт ошибку в общем формате сервиса.
_api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


async def require_api_key(
    settings: SettingsDep,
    provided: Annotated[str | None, Security(_api_key_header)] = None,
) -> None:
    expected = settings.api_key.get_secret_value()
    # compare_digest вместо ==, чтобы по времени сравнения нельзя было подобрать ключ.
    # Байты, а не строки: на не-ASCII заголовке строковый вариант падает с TypeError.
    if provided is None or not secrets.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Некорректный или отсутствующий заголовок {API_KEY_HEADER}",
        )


ApiKeyGuard = Depends(require_api_key)
