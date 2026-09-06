"""POST /finalize：读取搜索结果 → 生成推荐语 → TTS → 返回文字与音频。

降级策略：
  - 推荐语生成失败 → 502/504（由 deepseek_finalize 抛出 AppError）
  - TTS 或下载失败 → reply_text 保留，audio_url=null，warning 填原因
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from errors import AppError
from schemas import FinalizeData, FinalizeRequest, SuccessResponse
from services.bailian_tts import TtsDegradedError, synthesize_text
from services.deepseek_finalize import generate_recommendation
from services.search_store import get_stored_search
from services.tts_store import save_tts_audio

logger = logging.getLogger(__name__)

router = APIRouter()

FINALIZE_EXAMPLES = {
    "normal": {
        "summary": "正常：用已有 search_id 生成推荐语和语音",
        "value": {"search_id": "sch_20260906_171500_abc123"},
    },
    "not_found": {
        "summary": "search_id 不存在或已过期",
        "value": {"search_id": "sch_19000101_000000_000000"},
    },
}

_TTS_DEGRADED_MESSAGES: dict[str, str] = {
    "missing_api_key": "语音合成服务未配置，仅提供文字推荐",
    "tts_timeout": "语音合成超时，仅提供文字推荐",
    "tts_network_error": "语音合成服务暂时不可用，仅提供文字推荐",
    "tts_invalid_json": "语音合成返回异常，仅提供文字推荐",
    "tts_no_audio_url": "语音合成返回异常，仅提供文字推荐",
    "download_timeout": "音频下载超时，仅提供文字推荐",
    "download_network_error": "音频下载失败，仅提供文字推荐",
    "download_empty": "音频文件为空，仅提供文字推荐",
}

_DEFAULT_TTS_WARNING = "语音合成暂时不可用，仅提供文字推荐"


def _tts_warning(reason: str) -> str:
    for key, msg in _TTS_DEGRADED_MESSAGES.items():
        if reason.startswith(key):
            return msg
    return _DEFAULT_TTS_WARNING


@router.post(
    "/finalize",
    response_model=SuccessResponse[FinalizeData],
    summary="生成推荐语与语音",
    description=(
        "根据 `/search` 返回的 `search_id` 读取候选地点，调用 DeepSeek 生成推荐语，"
        "再经百炼 TTS 合成音频。TTS 失败时保留文字推荐并在 `warning` 中说明原因。"
    ),
    openapi_extra={"requestBody": {"content": {"application/json": {"examples": FINALIZE_EXAMPLES}}}},
)
async def finalize(body: FinalizeRequest, request: Request) -> JSONResponse:
    request_id: str = getattr(request.state, "request_id", "unknown")

    # ── 1. 读取存储的搜索结果 ──
    stored = get_stored_search(body.search_id, stage="finalize")
    record = stored.record

    pois: list[dict] = record.get("pois") or []
    if not pois:
        raise AppError(422, "NO_POI_CANDIDATES", "搜索结果中没有有效候选，请重新搜索", "finalize")

    first_poi = pois[0]
    name: str = first_poi.get("name") or ""
    address: str = first_poi.get("address") or ""
    distance_m: int = int(first_poi.get("distance_to_midpoint_m") or 0)
    category: str = record.get("category") or "咖啡店"

    if not name:
        raise AppError(422, "NO_POI_CANDIDATES", "候选地点缺少名称，请重新搜索", "finalize")

    logger.info(
        "stage=finalize search_id=%s poi=%s distance=%dm",
        body.search_id, name, distance_m,
    )

    # ── 2. DeepSeek 生成推荐语（失败抛 AppError → 502/504）──
    reply_text = await generate_recommendation(
        category=category,
        name=name,
        address=address,
        distance_m=distance_m,
    )

    # ── 3. 百炼 TTS + 音频下载（失败降级，保留文字）──
    audio_url: str | None = None
    warning: str | None = None

    try:
        audio_bytes, fmt = await synthesize_text(reply_text)
        stored_tts = save_tts_audio(audio_bytes, fmt=fmt, search_id=body.search_id)
        # 构造绝对 URL：使用请求的 base_url 保证协议/域名/端口一致
        base = str(request.base_url).rstrip("/")
        audio_url = f"{base}/audio/{stored_tts.audio_id}"
        logger.info("stage=finalize tts_ok audio_id=%s", stored_tts.audio_id)
    except TtsDegradedError as exc:
        warning = _tts_warning(exc.reason)
        logger.warning("stage=finalize tts_degraded reason=%s warning=%r", exc.reason, warning)

    return JSONResponse(
        content={
            "request_id": request_id,
            "data": {
                "reply_text": reply_text,
                "audio_url": audio_url,
                "warning": warning,
            },
        }
    )
