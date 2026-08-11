import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Response, status

from payment_service.api.deps import PaymentServiceDep
from payment_service.api.schemas import (
    ErrorResponse,
    PaymentAcceptedResponse,
    PaymentCreateRequest,
    PaymentResponse,
)
from payment_service.services.commands import NewPayment

router = APIRouter(prefix="/payments", tags=["payments"])

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
        description="Уникальный ключ запроса. Повтор с тем же ключом не создаёт новый платёж.",
    ),
]


def _to_command(request: PaymentCreateRequest) -> NewPayment:
    return NewPayment(
        amount=request.amount,
        currency=request.currency,
        description=request.description,
        metadata=request.metadata,
        webhook_url=str(request.webhook_url),
    )


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Создать платёж",
    responses={
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Ключ идемпотентности уже использован с другим телом запроса",
        }
    },
)
async def create_payment(
    request: PaymentCreateRequest,
    idempotency_key: IdempotencyKey,
    service: PaymentServiceDep,
    response: Response,
) -> PaymentAcceptedResponse:
    result = await service.create(_to_command(request), idempotency_key)
    # Клиенту полезно знать, что ответ пришёл из-за повтора, а не из-за нового платежа.
    response.headers["Idempotent-Replay"] = str(result.replayed).lower()
    return PaymentAcceptedResponse.from_model(result.payment)


@router.get(
    "/{payment_id}",
    summary="Получить платёж",
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}},
)
async def get_payment(payment_id: uuid.UUID, service: PaymentServiceDep) -> PaymentResponse:
    return PaymentResponse.from_model(await service.get(payment_id))
