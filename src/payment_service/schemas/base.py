from pydantic import BaseModel, ConfigDict


class BaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BaseResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
