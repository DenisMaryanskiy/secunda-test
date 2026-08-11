from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import cast

import structlog
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ExceptionHandler

from payment_service.api.schemas import ErrorResponse
from payment_service.errors import IdempotencyConflictError, PaymentNotFoundError

log = structlog.get_logger(__name__)

type Handler[E: Exception] = Callable[[Request, E], Awaitable[JSONResponse]]


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(code=code, message=message).model_dump(),
    )


async def _payment_not_found(_: Request, exc: PaymentNotFoundError) -> JSONResponse:
    return _error(status.HTTP_404_NOT_FOUND, "payment_not_found", str(exc))


async def _idempotency_conflict(_: Request, exc: IdempotencyConflictError) -> JSONResponse:
    return _error(status.HTTP_409_CONFLICT, "idempotency_conflict", str(exc))


async def _validation_failed(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "code": "validation_error",
            "message": "Тело запроса не прошло валидацию",
            "details": jsonable_encoder(exc.errors()),
        },
    )


async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
    # Ответы FastAPI по умолчанию выглядят как {"detail": ...}. Приводим их
    # к тому же виду, что и доменные ошибки, чтобы формат был один на весь API.
    code = HTTPStatus(exc.status_code).phrase.lower().replace(" ", "_")
    return _error(exc.status_code, code, str(exc.detail))


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    # Наружу отдаём только код: текст исключения может раскрыть детали внутренностей.
    log.exception("request.failed", path=request.url.path, error=type(exc).__name__)
    return _error(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_error",
        "Внутренняя ошибка сервиса",
    )


def register_error_handlers(app: FastAPI) -> None:
    # Starlette типизирует обработчики через голый Exception, поэтому приведение
    # типа здесь одно на всех, а сами обработчики остаются точно типизированными.
    def register[E: Exception](exc_type: type[E], handler: Handler[E]) -> None:
        app.add_exception_handler(exc_type, cast(ExceptionHandler, handler))

    register(PaymentNotFoundError, _payment_not_found)
    register(IdempotencyConflictError, _idempotency_conflict)
    register(RequestValidationError, _validation_failed)
    register(HTTPException, _http_error)
    register(Exception, _unhandled)
