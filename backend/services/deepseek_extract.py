import json
import logging
from pathlib import Path

import httpx
from pydantic import ValidationError

from config import settings
from errors import AppError
from schemas import ExtractData, ExtractModelOutput

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "extract.txt"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")

CITY_SUFFIXES = (
    "维吾尔自治区",
    "壮族自治区",
    "回族自治区",
    "自治区",
    "特别行政区",
    "省",
    "市",
)
CATEGORY_ALIASES = {
    "喝咖啡": "咖啡店",
    "咖啡": "咖啡店",
    "cafe": "咖啡店",
    "coffee": "咖啡店",
}
DEFAULT_CATEGORY = "咖啡店"
VAGUE_ADDRESSES = {
    "我家",
    "你家",
    "他家",
    "她家",
    "家",
    "家里",
    "家门口",
    "家附近",
    "我们家",
    "我家里",
    "公司",
    "单位",
    "公司里",
    "公司门口",
    "这儿",
    "这里",
    "这边",
    "这儿附近",
    "那儿",
    "那里",
    "那边",
    "附近",
}


def parse_model_output(raw: object) -> ExtractModelOutput:
    if not isinstance(raw, dict):
        logger.warning("stage=extract code=EXTRACT_FORMAT_ERROR reason=not_object")
        raise AppError(502, "EXTRACT_FORMAT_ERROR", "信息提取服务返回异常，请稍后重试", "extract")
    try:
        return ExtractModelOutput.model_validate(raw)
    except ValidationError:
        logger.warning("stage=extract code=EXTRACT_FORMAT_ERROR reason=schema")
        raise AppError(502, "EXTRACT_FORMAT_ERROR", "信息提取服务返回异常，请稍后重试", "extract") from None


def complete_or_raise(parsed: ExtractModelOutput, page_city: str) -> ExtractData:
    logger.info(
        "stage=extract diagnostics party_count=%s incomplete_reason=%s",
        parsed.party_count,
        parsed.incomplete_reason,
    )
    if parsed.party_count != 2:
        raise AppError(
            422,
            "INVALID_PARTY_COUNT",
            "当前版本仅支持两人约碰面，请重新表达",
            "extract",
        )

    address_a = _clean_address(parsed.address_a)
    address_b = _clean_address(parsed.address_b)
    if address_a is None or address_b is None:
        raise AppError(
            422,
            "INCOMPLETE_ADDRESS",
            "未能识别出双方的具体地点，请补充完整地址后重新录音",
            "extract",
        )

    city_a, city_b = _resolve_cities(parsed.city_a, parsed.city_b, page_city)
    if city_a != city_b:
        raise AppError(
            422,
            "CROSS_CITY",
            "当前版本仅支持同城碰面，请重新表达",
            "extract",
        )

    return ExtractData(
        city_a=city_a,
        address_a=address_a,
        city_b=city_b,
        address_b=address_b,
        category=_normalize_category(parsed.category),
    )


def _clean_optional(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _normalize_city(value: str | None) -> str | None:
    text = _clean_optional(value)
    if text is None:
        return None
    for suffix in CITY_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
            break
    return text


def _resolve_cities(city_a: str | None, city_b: str | None, page_city: str) -> tuple[str, str]:
    spoken_a = _normalize_city(city_a)
    spoken_b = _normalize_city(city_b)
    fallback = spoken_a or spoken_b or _normalize_city(page_city)
    if fallback is None:
        raise AppError(422, "VALIDATION_ERROR", "请求缺字段或字段类型错误", "extract")
    return spoken_a or fallback, spoken_b or fallback


def _clean_address(value: str | None) -> str | None:
    text = _clean_optional(value)
    if text is None or text in VAGUE_ADDRESSES:
        return None
    return text


def _normalize_category(value: str | None) -> str:
    text = _clean_optional(value)
    if text is None:
        return DEFAULT_CATEGORY
    return CATEGORY_ALIASES.get(text.lower(), CATEGORY_ALIASES.get(text, text))


def _loads_json_object(content: str) -> object:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```JSON").removeprefix("```")
        stripped = stripped.removesuffix("```").strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        logger.warning("stage=extract code=EXTRACT_FORMAT_ERROR reason=invalid_json")
        raise AppError(502, "EXTRACT_FORMAT_ERROR", "信息提取服务返回异常，请稍后重试", "extract") from None


def _message_content(body: object) -> tuple[str | None, str | None]:
    if not isinstance(body, dict):
        return None, None
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return None, None
    first = choices[0]
    if not isinstance(first, dict):
        return None, None
    finish_reason = first.get("finish_reason")
    reason = finish_reason if isinstance(finish_reason, str) else None
    message = first.get("message")
    if not isinstance(message, dict):
        return None, reason
    content = message.get("content")
    if isinstance(content, str):
        return content, reason
    return None, reason


async def extract_meeting(text: str, city: str) -> ExtractData:
    if not settings.deepseek_api_key:
        logger.warning("stage=extract code=EXTRACT_SERVICE_ERROR reason=missing_vendor_config")
        raise AppError(502, "EXTRACT_SERVICE_ERROR", "信息提取服务暂时不可用，请稍后重试", "extract")

    user_prompt = f"识别文字：{text}\n页面选定城市：{city}"
    request_body = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "max_tokens": settings.deepseek_max_tokens,
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(settings.timeout_extract_vendor)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                settings.deepseek_api_endpoint,
                headers=headers,
                json=request_body,
            )
    except httpx.TimeoutException:
        logger.warning("stage=extract code=EXTRACT_TIMEOUT")
        raise AppError(504, "EXTRACT_TIMEOUT", "信息提取超时，请稍后重试", "extract") from None
    except httpx.RequestError:
        logger.warning("stage=extract code=EXTRACT_SERVICE_ERROR reason=network")
        raise AppError(502, "EXTRACT_SERVICE_ERROR", "信息提取服务暂时不可用，请稍后重试", "extract") from None

    if response.status_code >= 400:
        logger.warning("stage=extract code=EXTRACT_SERVICE_ERROR vendor_status=%s", response.status_code)
        raise AppError(502, "EXTRACT_SERVICE_ERROR", "信息提取服务暂时不可用，请稍后重试", "extract")

    try:
        body = response.json()
    except ValueError:
        logger.warning("stage=extract code=EXTRACT_FORMAT_ERROR reason=invalid_json")
        raise AppError(502, "EXTRACT_FORMAT_ERROR", "信息提取服务返回异常，请稍后重试", "extract") from None

    if isinstance(body, dict) and body.get("error") and not body.get("choices"):
        logger.warning("stage=extract code=EXTRACT_SERVICE_ERROR vendor_error=true")
        raise AppError(502, "EXTRACT_SERVICE_ERROR", "信息提取服务暂时不可用，请稍后重试", "extract")

    content, finish_reason = _message_content(body)
    if finish_reason == "length":
        logger.warning("stage=extract code=EXTRACT_FORMAT_ERROR reason=truncated")
        raise AppError(502, "EXTRACT_FORMAT_ERROR", "信息提取服务返回异常，请稍后重试", "extract")
    if not isinstance(content, str) or not content.strip():
        logger.warning("stage=extract code=EXTRACT_FORMAT_ERROR reason=empty_content")
        raise AppError(502, "EXTRACT_FORMAT_ERROR", "信息提取服务返回异常，请稍后重试", "extract")

    parsed = parse_model_output(_loads_json_object(content))
    logger.info("stage=extract model_output=%s", parsed.model_dump())
    return complete_or_raise(parsed, city)
