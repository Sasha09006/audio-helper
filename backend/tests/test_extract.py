import json

import httpx
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from errors import AppError
from main import app
from schemas import ExtractData, ExtractModelOutput
from services import deepseek_extract


client = TestClient(app)

COMPLETE_MODEL = {
    "city_a": "杭州",
    "address_a": "杭州东站",
    "city_b": "杭州",
    "address_b": "西湖龙翔桥地铁站",
    "category": "咖啡店",
    "party_count": 2,
    "incomplete_reason": None,
}


def _model(**overrides) -> ExtractModelOutput:
    payload = {**COMPLETE_MODEL, **overrides}
    return ExtractModelOutput.model_validate(payload)


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakeClient:
    def __init__(self, response=None, error=None, capture=None):
        self._response = response
        self._error = error
        self._capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        if self._capture is not None:
            self._capture["url"] = url
            self._capture["headers"] = headers
            self._capture["json"] = json
        if self._error:
            raise self._error
        return self._response


def test_extract_missing_field():
    response = client.post("/extract", json={"text": "我在杭州东站"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["stage"] == "extract"
    assert "request_id" in body


def test_extract_blank_text():
    response = client.post("/extract", json={"text": "   ", "city": "杭州"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_extract_success_omits_diagnostics():
    data = ExtractData(
        city_a="杭州",
        address_a="杭州东站",
        city_b="杭州",
        address_b="西湖龙翔桥地铁站",
        category="咖啡店",
    )
    with patch("api.extract.extract_meeting", new_callable=AsyncMock, return_value=data):
        response = client.post(
            "/extract",
            json={
                "text": "我在杭州东站，朋友在西湖龙翔桥地铁站，帮我们找个中间的咖啡店",
                "city": "杭州",
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == {
        "city_a": "杭州",
        "address_a": "杭州东站",
        "city_b": "杭州",
        "address_b": "西湖龙翔桥地铁站",
        "category": "咖啡店",
    }
    assert "party_count" not in body["data"]
    assert "incomplete_reason" not in body["data"]
    assert "request_id" in body


def test_extract_party_count_error():
    with patch(
        "api.extract.extract_meeting",
        new_callable=AsyncMock,
        side_effect=AppError(
            422,
            "INVALID_PARTY_COUNT",
            "当前版本仅支持两人约碰面，请重新表达",
            "extract",
        ),
    ):
        response = client.post(
            "/extract",
            json={"text": "我、小明和小红都在西湖边，想找个餐厅", "city": "杭州"},
        )
    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "INVALID_PARTY_COUNT",
        "message": "当前版本仅支持两人约碰面，请重新表达",
        "stage": "extract",
    }


def test_complete_success_strips_city_suffix():
    parsed = _model(city_a="杭州市", city_b="杭州")
    data = deepseek_extract.complete_or_raise(parsed, "杭州")
    assert data.city_a == "杭州"
    assert data.city_b == "杭州"


def test_complete_page_city_when_both_null():
    parsed = _model(city_a=None, city_b=None, address_a="东站", address_b="龙翔桥地铁站")
    data = deepseek_extract.complete_or_raise(parsed, "杭州")
    assert data.city_a == "杭州"
    assert data.city_b == "杭州"


def test_complete_spoken_city_overrides_page_city():
    parsed = _model(
        city_a="上海",
        address_a="人民广场",
        city_b="上海",
        address_b="静安寺",
        category="餐厅",
    )
    data = deepseek_extract.complete_or_raise(parsed, "杭州")
    assert data.city_a == "上海"
    assert data.city_b == "上海"
    assert data.category == "餐厅"


def test_complete_one_spoken_city_fills_the_other():
    parsed = _model(city_a="上海", city_b=None, address_a="人民广场", address_b="静安寺")
    data = deepseek_extract.complete_or_raise(parsed, "杭州")
    assert data.city_a == "上海"
    assert data.city_b == "上海"


def test_complete_category_alias_and_default():
    parsed = _model(category="喝咖啡")
    assert deepseek_extract.complete_or_raise(parsed, "杭州").category == "咖啡店"
    parsed_null = _model(category=None)
    assert deepseek_extract.complete_or_raise(parsed_null, "杭州").category == "咖啡店"


def test_complete_invalid_party_count():
    parsed = _model(party_count=3, address_a="西湖边", address_b=None, incomplete_reason="人数不是两人")
    with pytest.raises(AppError) as exc:
        deepseek_extract.complete_or_raise(parsed, "杭州")
    assert exc.value.code == "INVALID_PARTY_COUNT"
    assert exc.value.status_code == 422


def test_complete_missing_address():
    parsed = _model(address_a=None, address_b=None, incomplete_reason="缺少双方地址")
    with pytest.raises(AppError) as exc:
        deepseek_extract.complete_or_raise(parsed, "杭州")
    assert exc.value.code == "INCOMPLETE_ADDRESS"


def test_complete_vague_home_address():
    parsed = _model(address_a="我家", address_b="公司", incomplete_reason="地址含糊")
    with pytest.raises(AppError) as exc:
        deepseek_extract.complete_or_raise(parsed, "杭州")
    assert exc.value.code == "INCOMPLETE_ADDRESS"


def test_complete_cross_city():
    parsed = _model(
        city_a="杭州",
        address_a="杭州东站",
        city_b="上海",
        address_b="上海虹桥站",
        incomplete_reason="跨城",
    )
    with pytest.raises(AppError) as exc:
        deepseek_extract.complete_or_raise(parsed, "杭州")
    assert exc.value.code == "CROSS_CITY"


def test_parse_missing_field_is_format_error_not_incomplete():
    raw = {
        "city_a": "杭州",
        "address_a": "杭州东站",
        "city_b": "杭州",
        "address_b": "西湖龙翔桥地铁站",
        "category": "咖啡店",
    }
    with pytest.raises(AppError) as exc:
        deepseek_extract.parse_model_output(raw)
    assert exc.value.code == "EXTRACT_FORMAT_ERROR"
    assert exc.value.status_code == 502
    assert exc.value.code != "INCOMPLETE_ADDRESS"


def test_parse_wrong_type_is_format_error():
    raw = {**COMPLETE_MODEL, "party_count": "两人"}
    with pytest.raises(AppError) as exc:
        deepseek_extract.parse_model_output(raw)
    assert exc.value.code == "EXTRACT_FORMAT_ERROR"


def test_parse_not_object_is_format_error():
    with pytest.raises(AppError) as exc:
        deepseek_extract.parse_model_output(["杭州东站"])
    assert exc.value.code == "EXTRACT_FORMAT_ERROR"


@pytest.mark.asyncio
async def test_extract_timeout(monkeypatch):
    monkeypatch.setattr(deepseek_extract.settings, "deepseek_api_key", "sk-test")
    monkeypatch.setattr(
        deepseek_extract.httpx,
        "AsyncClient",
        lambda timeout=None: FakeClient(error=httpx.TimeoutException("timeout")),
    )
    with pytest.raises(AppError) as exc:
        await deepseek_extract.extract_meeting("我在杭州东站", "杭州")
    assert exc.value.status_code == 504
    assert exc.value.code == "EXTRACT_TIMEOUT"


@pytest.mark.asyncio
async def test_extract_vendor_error(monkeypatch):
    monkeypatch.setattr(deepseek_extract.settings, "deepseek_api_key", "sk-test")
    monkeypatch.setattr(
        deepseek_extract.httpx,
        "AsyncClient",
        lambda timeout=None: FakeClient(response=FakeResponse(status_code=500, body={"error": {"message": "boom"}})),
    )
    with pytest.raises(AppError) as exc:
        await deepseek_extract.extract_meeting("我在杭州东站", "杭州")
    assert exc.value.status_code == 502
    assert exc.value.code == "EXTRACT_SERVICE_ERROR"


@pytest.mark.asyncio
async def test_extract_missing_key(monkeypatch):
    monkeypatch.setattr(deepseek_extract.settings, "deepseek_api_key", "")
    with pytest.raises(AppError) as exc:
        await deepseek_extract.extract_meeting("我在杭州东站", "杭州")
    assert exc.value.code == "EXTRACT_SERVICE_ERROR"


@pytest.mark.asyncio
async def test_extract_empty_content_is_format_error(monkeypatch):
    monkeypatch.setattr(deepseek_extract.settings, "deepseek_api_key", "sk-test")
    body = {"choices": [{"finish_reason": "stop", "message": {"content": "   "}}]}
    monkeypatch.setattr(
        deepseek_extract.httpx,
        "AsyncClient",
        lambda timeout=None: FakeClient(response=FakeResponse(body=body)),
    )
    with pytest.raises(AppError) as exc:
        await deepseek_extract.extract_meeting("我在杭州东站", "杭州")
    assert exc.value.code == "EXTRACT_FORMAT_ERROR"


@pytest.mark.asyncio
async def test_extract_invalid_json_is_format_error(monkeypatch):
    monkeypatch.setattr(deepseek_extract.settings, "deepseek_api_key", "sk-test")
    body = {"choices": [{"finish_reason": "stop", "message": {"content": "not-json"}}]}
    monkeypatch.setattr(
        deepseek_extract.httpx,
        "AsyncClient",
        lambda timeout=None: FakeClient(response=FakeResponse(body=body)),
    )
    with pytest.raises(AppError) as exc:
        await deepseek_extract.extract_meeting("我在杭州东站", "杭州")
    assert exc.value.code == "EXTRACT_FORMAT_ERROR"


@pytest.mark.asyncio
async def test_extract_vendor_request_uses_json_mode(monkeypatch):
    monkeypatch.setattr(deepseek_extract.settings, "deepseek_api_key", "sk-test")
    capture = {}
    content = json.dumps(COMPLETE_MODEL, ensure_ascii=False)
    body = {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
    monkeypatch.setattr(
        deepseek_extract.httpx,
        "AsyncClient",
        lambda timeout=None: FakeClient(response=FakeResponse(body=body), capture=capture),
    )
    data = await deepseek_extract.extract_meeting(
        "我在杭州东站，朋友在西湖龙翔桥地铁站，帮我们找个中间的咖啡店",
        "杭州",
    )
    assert data.address_a == "杭州东站"
    assert capture["json"]["thinking"] == {"type": "disabled"}
    assert capture["json"]["response_format"] == {"type": "json_object"}
    assert capture["json"]["model"] == deepseek_extract.settings.deepseek_model
    assert "json" in capture["json"]["messages"][0]["content"].lower()
    assert capture["headers"]["Authorization"].startswith("Bearer ")
