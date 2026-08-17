from pydantic import ConfigDict

from payment_service.schemas.base import BaseResponse


class ErrorResponse(BaseResponse):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "code": "payment_not_found",
                    "message": "Платёж 88fff74e-6f1a-4d5b-9c2e-7a1b3c4d5e6f не найден",
                }
            ]
        }
    )

    code: str
    message: str
