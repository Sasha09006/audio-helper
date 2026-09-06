import logging
import time

from fastapi import APIRouter, File, Request, UploadFile

from config import settings
from errors import AppError
from schemas import ErrorResponse, SuccessResponse, UploadData
from services.audio_store import save_audio
from services.webm_opus import UnsupportedAudioFormat, probe_webm_opus

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/upload",
    response_model=SuccessResponse[UploadData],
    responses={
        413: {"model": ErrorResponse},
        415: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
    summary="上传录音",
)
async def upload_audio(
    request: Request,
    file: UploadFile = File(..., description="录音文件，字段名必须为 file"),
) -> SuccessResponse[UploadData]:
    started = time.perf_counter()
    max_bytes = settings.max_audio_size_mb * 1024 * 1024
    _reject_oversized_body(request, max_bytes)

    payload = await file.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise AppError(413, "FILE_TOO_LARGE", "音频文件超过5MB限制", "upload")
    if not payload:
        raise AppError(422, "MISSING_FILE", "未检测到音频文件", "upload")

    try:
        probe = probe_webm_opus(payload)
    except UnsupportedAudioFormat:
        logger.warning("stage=upload code=UNSUPPORTED_FORMAT")
        raise AppError(415, "UNSUPPORTED_FORMAT", "仅支持WebM/Opus格式音频", "upload") from None

    min_sec = settings.min_audio_duration_sec
    max_sec = settings.max_audio_duration_sec
    if probe.duration_sec < min_sec or probe.duration_sec > max_sec:
        logger.warning(
            "stage=upload code=INVALID_DURATION duration_sec=%.3f source=%s",
            probe.duration_sec,
            probe.duration_source,
        )
        raise AppError(422, "INVALID_DURATION", "录音时长必须在1-60秒之间", "upload")

    stored = save_audio(payload, probe=probe, original_filename=file.filename)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "stage=upload elapsed_ms=%s audio_id=%s size=%s duration_sec=%.3f source=%s",
        elapsed_ms,
        stored.audio_id,
        stored.byte_size,
        stored.duration_sec,
        stored.duration_source,
    )
    return SuccessResponse(
        request_id=request.state.request_id,
        data=UploadData(audio_id=stored.audio_id),
    )


def _reject_oversized_body(request: Request, max_bytes: int) -> None:
    content_length = request.headers.get("content-length")
    if not content_length:
        return
    try:
        length = int(content_length)
    except ValueError:
        return
    multipart_overhead = 256 * 1024
    if length > max_bytes + multipart_overhead:
        raise AppError(413, "FILE_TOO_LARGE", "音频文件超过5MB限制", "upload")
