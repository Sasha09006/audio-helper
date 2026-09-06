"""GET /audio/{audio_id}：按 audio_id 前缀分发到对应音频存储，返回二进制音频。

- tts_{…}  → tts_store（TTS 合成音频）
- 其他前缀  → 404
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from services.tts_store import TTS_ID_RE, get_stored_tts_audio

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/audio/{audio_id}",
    summary="获取音频文件",
    description=(
        "成功时返回音频二进制，`Content-Type` 与供应商实际格式一致（如 `audio/wav`）。\n\n"
        "失败时返回统一 JSON 错误体：`{request_id, error:{code, message, stage}}`。"
    ),
    responses={
        200: {"content": {"audio/wav": {}, "audio/mpeg": {}, "audio/ogg": {}}},
        404: {"description": "音频不存在或已过期"},
    },
)
async def get_audio(audio_id: str, request: Request) -> Response:
    request_id: str = getattr(request.state, "request_id", "unknown")

    # 目前只支持 TTS 生成音频（tts_ 前缀）
    if not TTS_ID_RE.fullmatch(audio_id):
        logger.warning("stage=audio code=AUDIO_NOT_FOUND audio_id=%r", audio_id)
        return JSONResponse(
            status_code=404,
            content={
                "request_id": request_id,
                "error": {
                    "code": "AUDIO_NOT_FOUND",
                    "message": "音频文件不存在或已过期",
                    "stage": "audio",
                },
            },
        )

    try:
        stored = get_stored_tts_audio(audio_id, stage="audio")
    except Exception as exc:
        # AppError 已有统一 handler，这里只处理不预期的异常
        from errors import AppError
        if isinstance(exc, AppError):
            raise
        logger.exception("stage=audio unexpected_error audio_id=%s", audio_id)
        return JSONResponse(
            status_code=500,
            content={
                "request_id": request_id,
                "error": {
                    "code": "AUDIO_READ_ERROR",
                    "message": "音频读取失败，请稍后重试",
                    "stage": "audio",
                },
            },
        )

    audio_bytes = stored.file_path.read_bytes()
    logger.info("stage=audio serve audio_id=%s bytes=%d", audio_id, len(audio_bytes))
    return Response(
        content=audio_bytes,
        media_type=stored.content_type,
        headers={"Content-Disposition": f'inline; filename="{audio_id}.{stored.file_path.suffix.lstrip(".")}"'},
    )
