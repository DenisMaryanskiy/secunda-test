from pydantic import ConfigDict

from payment_service.schemas.base import BaseResponse


class HealthResponse(BaseResponse):
    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ok"}]})

    status: str
