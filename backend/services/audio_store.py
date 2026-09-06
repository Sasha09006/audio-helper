import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from config import settings
from errors import AppError
from services.webm_opus import WebmOpusProbe

logger = logging.getLogger(__name__)

AUDIO_PREFIX = "aud"
AUDIO_ID_RE = re.compile(r"^aud_[0-9]{8}_[0-9]{6}_[0-9a-f]{6}$")


@dataclass(frozen=True)
class StoredAudio:
    audio_id: str
    created_at: datetime
    file_path: Path
    meta_path: Path
    duration_sec: float
    byte_size: int
    duration_source: str


def uploads_dir() -> Path:
    path = settings.storage_dir / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def generate_audio_id(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"{AUDIO_PREFIX}_{stamp}_{uuid.uuid4().hex[:6]}"


def save_audio(
    payload: bytes,
    *,
    probe: WebmOpusProbe,
    original_filename: str | None,
) -> StoredAudio:
    created_at = datetime.now().astimezone()
    audio_id = generate_audio_id(created_at)
    directory = uploads_dir()
    file_path = directory / f"{audio_id}.webm"
    meta_path = directory / f"{audio_id}.json"
    record = {
        "audio_id": audio_id,
        "created_at": created_at.isoformat(),
        "byte_size": len(payload),
        "duration_sec": probe.duration_sec,
        "duration_source": probe.duration_source,
        "container": "webm",
        "codec": "opus",
        "original_filename": original_filename,
    }
    file_path.write_bytes(payload)
    try:
        meta_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        file_path.unlink(missing_ok=True)
        raise
    return StoredAudio(
        audio_id=audio_id,
        created_at=created_at,
        file_path=file_path,
        meta_path=meta_path,
        duration_sec=probe.duration_sec,
        byte_size=len(payload),
        duration_source=probe.duration_source,
    )


def get_stored_audio(audio_id: str, *, stage: str = "asr") -> StoredAudio:
    if not AUDIO_ID_RE.fullmatch(audio_id):
        raise AppError(404, "AUDIO_NOT_FOUND", "音频文件不存在或已过期", stage)

    directory = uploads_dir().resolve()
    file_path = (directory / f"{audio_id}.webm").resolve()
    meta_path = (directory / f"{audio_id}.json").resolve()
    if file_path.parent != directory or meta_path.parent != directory:
        raise AppError(404, "AUDIO_NOT_FOUND", "音频文件不存在或已过期", stage)
    if not meta_path.is_file() or not file_path.is_file():
        raise AppError(404, "AUDIO_NOT_FOUND", "音频文件不存在或已过期", stage)

    created_at = _created_at_from_meta(meta_path)
    if created_at is None:
        raise AppError(404, "AUDIO_NOT_FOUND", "音频文件不存在或已过期", stage)
    if created_at.tzinfo is None:
        created_at = created_at.astimezone()
    now = datetime.now().astimezone()
    if now - created_at >= timedelta(hours=settings.temp_data_ttl_hours):
        raise AppError(404, "AUDIO_NOT_FOUND", "音频文件不存在或已过期", stage)

    try:
        record = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AppError(404, "AUDIO_NOT_FOUND", "音频文件不存在或已过期", stage) from exc

    return StoredAudio(
        audio_id=audio_id,
        created_at=created_at,
        file_path=file_path,
        meta_path=meta_path,
        duration_sec=float(record.get("duration_sec") or 0),
        byte_size=int(record.get("byte_size") or file_path.stat().st_size),
        duration_source=str(record.get("duration_source") or ""),
    )


def purge_expired_audio() -> int:
    directory = uploads_dir()
    ttl = timedelta(hours=settings.temp_data_ttl_hours)
    now = datetime.now().astimezone()
    removed = 0
    for meta_path in directory.glob("*.json"):
        created_at = _created_at_from_meta(meta_path)
        if created_at is None or now - created_at < ttl:
            continue
        audio_path = meta_path.with_suffix(".webm")
        audio_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        removed += 1
    if removed:
        logger.info("stage=upload purged_expired=%s", removed)
    return removed


def _created_at_from_meta(meta_path: Path) -> datetime | None:
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        raw = payload.get("created_at")
        if not raw:
            return None
        return datetime.fromisoformat(raw)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
