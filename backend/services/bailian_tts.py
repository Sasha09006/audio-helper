"""百炼 TTS 服务：文本 → 合成音频。

流程：
  1. POST 到 DashScope multimodal-generation 接口，获取 output.audio.url 和 format。
  2. 从 OSS URL 下载音频二进制。
  3. 返回 (bytes, format_str)，由调用方保存到 tts_store。

失败策略（供调用方决定降级还是报错）：
  - 推荐语生成失败已在 deepseek_finalize 层处理（502/504）。
  - TTS 调用失败 / 下载失败：本函数抛出 TtsDegradedError（非 AppError），
    让 /finalize 路由捕获后降级为仅返回文字结果，不再向前端报错。
"""

import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)


class TtsDegradedError(Exception):
    """TTS 调用或音频下载失败，触发文字降级，不应作为 502 上报给用户。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _extract_audio_info(body: object) -> tuple[str, str] | None:
    """从响应体中提取 (url, format)，失败返回 None。"""
    if not isinstance(body, dict):
        return None
    output = body.get("output")
    if not isinstance(output, dict):
        return None
    audio = output.get("audio")
    if not isinstance(audio, dict):
        return None
    url = audio.get("url")
    fmt = audio.get("format") or "wav"
    if not isinstance(url, str) or not url.startswith("http"):
        return None
    return url, str(fmt)


async def synthesize_text(text: str) -> tuple[bytes, str]:
    """调用百炼 TTS 合成文本，下载并返回 (audio_bytes, format_str)。

    任何失败都抛出 TtsDegradedError，让上层降级处理。
    """
    if not settings.bailian_api_key:
        logger.warning("stage=tts reason=missing_api_key")
        raise TtsDegradedError("missing_api_key")

    request_body = {
        "model": settings.bailian_tts_model,
        "input": {
            "text": text,
            "voice": settings.bailian_tts_voice,
            "language_type": "Chinese",
        },
    }
    headers = {
        "Authorization": f"Bearer {settings.bailian_api_key}",
        "Content-Type": "application/json",
    }
    tts_timeout = httpx.Timeout(settings.timeout_finalize_tts)

    # ── Step 1：调用 TTS 接口，获取 OSS 音频 URL ──
    try:
        async with httpx.AsyncClient(timeout=tts_timeout) as client:
            response = await client.post(
                settings.bailian_tts_endpoint,
                headers=headers,
                json=request_body,
            )
    except httpx.TimeoutException:
        logger.warning("stage=tts reason=tts_timeout")
        raise TtsDegradedError("tts_timeout") from None
    except httpx.RequestError as exc:
        logger.warning("stage=tts reason=tts_network_error err=%s", exc)
        raise TtsDegradedError("tts_network_error") from None

    if response.status_code >= 400:
        logger.warning("stage=tts reason=tts_vendor_error status=%s", response.status_code)
        raise TtsDegradedError(f"tts_vendor_status_{response.status_code}")

    try:
        body = response.json()
    except ValueError:
        logger.warning("stage=tts reason=tts_invalid_json")
        raise TtsDegradedError("tts_invalid_json") from None

    info = _extract_audio_info(body)
    if info is None:
        logger.warning("stage=tts reason=tts_no_audio_url body=%s", str(body)[:200])
        raise TtsDegradedError("tts_no_audio_url")

    audio_url, fmt = info
    logger.info("stage=tts audio_url=%s fmt=%s", audio_url[:80], fmt)

    # ── Step 2：下载 OSS 音频文件 ──
    dl_timeout = httpx.Timeout(settings.timeout_finalize_download)
    try:
        async with httpx.AsyncClient(timeout=dl_timeout) as client:
            dl_response = await client.get(audio_url)
    except httpx.TimeoutException:
        logger.warning("stage=tts reason=download_timeout")
        raise TtsDegradedError("download_timeout") from None
    except httpx.RequestError as exc:
        logger.warning("stage=tts reason=download_network_error err=%s", exc)
        raise TtsDegradedError("download_network_error") from None

    if dl_response.status_code >= 400:
        logger.warning("stage=tts reason=download_error status=%s", dl_response.status_code)
        raise TtsDegradedError(f"download_status_{dl_response.status_code}")

    audio_bytes = dl_response.content
    if not audio_bytes:
        logger.warning("stage=tts reason=download_empty")
        raise TtsDegradedError("download_empty")

    logger.info("stage=tts downloaded bytes=%d fmt=%s", len(audio_bytes), fmt)
    return audio_bytes, fmt
