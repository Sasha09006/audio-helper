from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class SuccessResponse(BaseModel, Generic[T]):
    request_id: str
    data: T


class HealthData(BaseModel):
    status: str = Field(examples=["ok"])


class UploadData(BaseModel):
    audio_id: str = Field(examples=["aud_20260906_153000_abc123"])


class ErrorDetail(BaseModel):
    code: str
    message: str
    stage: str


class ErrorResponse(BaseModel):
    request_id: str
    error: ErrorDetail
