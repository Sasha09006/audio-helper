from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

T = TypeVar("T")


class SuccessResponse(BaseModel, Generic[T]):
    request_id: str
    data: T


class HealthData(BaseModel):
    status: str = Field(examples=["ok"])


class UploadData(BaseModel):
    audio_id: str = Field(examples=["aud_20260906_153000_abc123"])


class AsrRequest(BaseModel):
    audio_id: str = Field(examples=["aud_20260906_153000_abc123"])


class AsrData(BaseModel):
    text: str = Field(examples=["我在杭州东站，朋友在西湖龙翔桥地铁站，帮我们找个中间的咖啡店"])


class ExtractRequest(BaseModel):
    text: str = Field(
        examples=["我在杭州东站，朋友在西湖龙翔桥地铁站，帮我们找个中间的咖啡店"]
    )
    city: str = Field(examples=["杭州"])

    @field_validator("text", "city")
    @classmethod
    def not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("blank")
        return cleaned


class ExtractModelOutput(BaseModel):
    """DeepSeek 内部 JSON。七个字段都必须出现；除 party_count 外允许 null。"""

    model_config = ConfigDict(extra="ignore")

    city_a: str | None
    address_a: str | None
    city_b: str | None
    address_b: str | None
    category: str | None
    party_count: int
    incomplete_reason: str | None


class ExtractData(BaseModel):
    city_a: str = Field(examples=["杭州"])
    address_a: str = Field(examples=["杭州东站"])
    city_b: str = Field(examples=["杭州"])
    address_b: str = Field(examples=["西湖龙翔桥地铁站"])
    category: str = Field(examples=["咖啡店"])


class ErrorDetail(BaseModel):
    code: str
    message: str
    stage: str


class ErrorResponse(BaseModel):
    request_id: str
    error: ErrorDetail
