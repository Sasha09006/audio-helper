import logging
import time

from fastapi import APIRouter, Request

from errors import AppError
from schemas import AsrData, AsrRequest, ErrorResponse, SuccessResponse
from services.audio_store import get_stored_audio
from services.bailian_asr import transcribe_audio

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/asr",
    response_model=SuccessResponse[AsrData],
    responses={
        404: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
    summary="语音识别",
)
async def recognize_audio(
    request: Request,
    payload: AsrRequest,
) -> SuccessResponse[AsrData]:
    started = time.perf_counter()
    stored = get_stored_audio(payload.audio_id, stage="asr")
    try:
        audio_bytes = stored.file_path.read_bytes()
    except OSError:
        raise AppError(404, "AUDIO_NOT_FOUND", "音频文件不存在或已过期", "asr") from None

    text = await transcribe_audio(audio_bytes)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "stage=asr elapsed_ms=%s audio_id=%s text_len=%s",
        elapsed_ms,
        stored.audio_id,
        len(text),
    )
    return SuccessResponse(
        request_id=request.state.request_id,
        data=AsrData(text=text),
    )
