from fastapi import APIRouter

from payment_service.api.dependencies.security import ApiKeyGuard
from payment_service.api.routers import payments

api_router = APIRouter(prefix="/api/v1", dependencies=[ApiKeyGuard])
api_router.include_router(payments.router)

__all__ = ["api_router"]
