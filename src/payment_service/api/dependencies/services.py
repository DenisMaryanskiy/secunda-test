from typing import Annotated

from fastapi import Depends

from payment_service.api.dependencies.database import SessionDep
from payment_service.services.payments import PaymentService


def get_payment_service(session: SessionDep) -> PaymentService:
    return PaymentService(session)


PaymentServiceDep = Annotated[PaymentService, Depends(get_payment_service)]
