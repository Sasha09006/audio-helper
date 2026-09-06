from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class SuccessResponse(BaseModel, Generic[T]):
    request_id: str
    data: T


class HealthData(BaseModel):
    status: str = Field(examples=["ok"])
