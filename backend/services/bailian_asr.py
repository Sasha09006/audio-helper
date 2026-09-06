import base64
import logging

import httpx

from config import settings
from errors import AppError

logger = logging.getLogger(__name__)

AUDIO_DATA_URI_PREFIX = "data:audio/webm;base64,"


def encode_audio_data_uri(payload: bytes) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    data_uri = f"{AUDIO_DATA_URI_PREFIX}{encoded}"
    if len(data_uri.encode("utf-8")) > settings.bailian_asr_encoded_max_bytes:
        raise AppError(413, "FILE_TOO_LARGE", "音频编码后超过识别服务限制", "asr")
    return data_uri


def _extract_text(body: object) -> str | None:
    if not isinstance(body, dict):
        return None
    output = body.get("output")
    if not isinstance(output, dict):
        return None
    choices = output.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    if not parts:
        return None
    return "".join(parts)


async def transcribe_audio(payload: bytes) -> str:
    if not settings.bailian_api_key:
        logger.warning("stage=asr code=ASR_SERVICE_ERROR reason=missing_vendor_config")
        raise AppError(502, "ASR_SERVICE_ERROR", "语音识别服务暂时不可用，请稍后重试", "asr")

    data_uri = encode_audio_data_uri(payload)
    request_body = {
        "model": settings.bailian_asr_model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [{"audio": data_uri}],
                }
            ]
        },
        "parameters": {
            "asr_options": {
                "enable_itn": False,
            }
        },
    }
    headers = {
        "Authorization": f"Bearer {settings.bailian_api_key}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(settings.timeout_asr_vendor)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                settings.bailian_asr_endpoint,
                headers=headers,
                json=request_body,
            )
    except httpx.TimeoutException:
        logger.warning("stage=asr code=ASR_TIMEOUT")
        raise AppError(504, "ASR_TIMEOUT", "语音识别超时，请稍后重试", "asr") from None
    except httpx.RequestError:
        logger.warning("stage=asr code=ASR_SERVICE_ERROR reason=network")
        raise AppError(502, "ASR_SERVICE_ERROR", "语音识别服务暂时不可用，请稍后重试", "asr") from None

    if response.status_code >= 400:
        logger.warning("stage=asr code=ASR_SERVICE_ERROR vendor_status=%s", response.status_code)
        raise AppError(502, "ASR_SERVICE_ERROR", "语音识别服务暂时不可用，请稍后重试", "asr")

    try:
        body = response.json()
    except ValueError:
        logger.warning("stage=asr code=ASR_SERVICE_ERROR reason=invalid_json")
        raise AppError(502, "ASR_SERVICE_ERROR", "语音识别服务暂时不可用，请稍后重试", "asr") from None

    if isinstance(body, dict) and body.get("code") and not body.get("output"):
        logger.warning("stage=asr code=ASR_SERVICE_ERROR vendor_code=%s", body.get("code"))
        raise AppError(502, "ASR_SERVICE_ERROR", "语音识别服务暂时不可用，请稍后重试", "asr")

    text = _extract_text(body)
    if text is None:
        logger.warning("stage=asr code=ASR_SERVICE_ERROR reason=missing_text")
        raise AppError(502, "ASR_SERVICE_ERROR", "语音识别服务暂时不可用，请稍后重试", "asr")

    cleaned = text.strip()
    if not cleaned:
        raise AppError(422, "EMPTY_RECOGNITION", "未识别到有效语音内容，请重新录音", "asr")
    return cleaned
