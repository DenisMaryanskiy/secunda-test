from fastapi import APIRouter

from payment_service.schemas.health import HealthResponse

router = APIRouter(tags=["service"])


@router.get("/health", summary="Проверка состояния сервиса")
async def health() -> HealthResponse:
    return HealthResponse(status="ok")
