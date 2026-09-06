"""DeepSeek 推荐语生成服务。

输入：POI 候选信息（名称、地址、距中点距离、碰面类别）
输出：一段自然的中文推荐语文本（str）
失败：502 RECOMMEND_SERVICE_ERROR / 504 RECOMMEND_TIMEOUT
"""

import logging
from pathlib import Path

import httpx

from config import settings
from errors import AppError

logger = logging.getLogger(__name__)

_PROMPT_FILE = Path(__file__).parent.parent / "prompts" / "finalize.txt"
SYSTEM_PROMPT: str = _PROMPT_FILE.read_text(encoding="utf-8").strip()


def _build_user_prompt(
    *,
    category: str,
    name: str,
    address: str,
    distance_m: int,
) -> str:
    return (
        f"碰面类别：{category}\n"
        f"推荐地点：\n"
        f"名称：{name}\n"
        f"地址：{address}\n"
        f"距两人中间位置：约 {distance_m} 米"
    )


def _extract_text_from_body(body: object) -> str | None:
    """从 DeepSeek 响应中提取文本内容和 finish_reason。"""
    if not isinstance(body, dict):
        return None
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    finish_reason = first.get("finish_reason")
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content if finish_reason != "length" else None
    return None


async def generate_recommendation(
    *,
    category: str,
    name: str,
    address: str,
    distance_m: int,
) -> str:
    """调用 DeepSeek 生成推荐语，失败抛出 AppError。"""
    if not settings.deepseek_api_key:
        logger.warning("stage=finalize code=RECOMMEND_SERVICE_ERROR reason=missing_vendor_config")
        raise AppError(502, "RECOMMEND_SERVICE_ERROR", "推荐语生成服务暂时不可用，请稍后重试", "finalize")

    user_prompt = _build_user_prompt(
        category=category,
        name=name,
        address=address,
        distance_m=distance_m,
    )
    request_body = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "thinking": {"type": "disabled"},
        "max_tokens": 256,
        "temperature": 0.7,
    }
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(settings.timeout_finalize_recommend)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                settings.deepseek_api_endpoint,
                headers=headers,
                json=request_body,
            )
    except httpx.TimeoutException:
        logger.warning("stage=finalize code=RECOMMEND_TIMEOUT")
        raise AppError(504, "RECOMMEND_TIMEOUT", "推荐语生成超时，请稍后重试", "finalize") from None
    except httpx.RequestError:
        logger.warning("stage=finalize code=RECOMMEND_SERVICE_ERROR reason=network")
        raise AppError(502, "RECOMMEND_SERVICE_ERROR", "推荐语生成服务暂时不可用，请稍后重试", "finalize") from None

    if response.status_code >= 400:
        logger.warning("stage=finalize code=RECOMMEND_SERVICE_ERROR vendor_status=%s", response.status_code)
        raise AppError(502, "RECOMMEND_SERVICE_ERROR", "推荐语生成服务暂时不可用，请稍后重试", "finalize")

    try:
        body = response.json()
    except ValueError:
        logger.warning("stage=finalize code=RECOMMEND_SERVICE_ERROR reason=invalid_json")
        raise AppError(502, "RECOMMEND_SERVICE_ERROR", "推荐语生成服务暂时不可用，请稍后重试", "finalize") from None

    if isinstance(body, dict) and body.get("error") and not body.get("choices"):
        logger.warning("stage=finalize code=RECOMMEND_SERVICE_ERROR vendor_error=true")
        raise AppError(502, "RECOMMEND_SERVICE_ERROR", "推荐语生成服务暂时不可用，请稍后重试", "finalize")

    text = _extract_text_from_body(body)
    if not text or not text.strip():
        logger.warning("stage=finalize code=RECOMMEND_SERVICE_ERROR reason=empty_or_truncated")
        raise AppError(502, "RECOMMEND_SERVICE_ERROR", "推荐语生成服务暂时不可用，请稍后重试", "finalize")

    result = text.strip()
    logger.info("stage=finalize recommend_text=%r", result[:80])
    return result
