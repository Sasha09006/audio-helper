"""TTS 生成音频的存储与读取。

文件放在 storage/audio_tts/，ID 格式：tts_{date}_{time}_{hex6}
音频二进制与 metadata JSON 各一个文件，扩展名依据供应商返回的真实格式。
"""

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from config import settings
from errors import AppError

logger = logging.getLogger(__name__)

TTS_PREFIX = "tts"
TTS_ID_RE = re.compile(r"^tts_[0-9]{8}_[0-9]{6}_[0-9a-f]{6}$")

# 供应商 format 字段 -> MIME Content-Type
_FORMAT_CT: dict[str, str] = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "pcm": "audio/L16;rate=24000",
}
# 允许直接作为扩展名的格式
_KNOWN_EXTS = {"wav", "mp3", "ogg"}


@dataclass(frozen=True)
class StoredTtsAudio:
    audio_id: str
    created_at: datetime
    file_path: Path
    meta_path: Path
    content_type: str
    byte_size: int


def tts_dir() -> Path:
    path = settings.storage_dir / "audio_tts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def generate_tts_id(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"{TTS_PREFIX}_{stamp}_{uuid.uuid4().hex[:6]}"


def _ext_for(fmt: str) -> str:
    """将供应商 format 字段映射为文件扩展名，未知格式回退到 wav。"""
    return fmt.lower() if fmt.lower() in _KNOWN_EXTS else "wav"


def save_tts_audio(payload: bytes, *, fmt: str, search_id: str) -> StoredTtsAudio:
    """保存 TTS 音频二进制，返回 StoredTtsAudio。fmt 为供应商返回的真实格式字符串。"""
    created_at = datetime.now().astimezone()
    audio_id = generate_tts_id(created_at)
    directory = tts_dir()
    content_type = _FORMAT_CT.get(fmt.lower(), "audio/wav")
    ext = _ext_for(fmt)
    file_path = directory / f"{audio_id}.{ext}"
    meta_path = directory / f"{audio_id}.json"
    record = {
        "audio_id": audio_id,
        "created_at": created_at.isoformat(),
        "format": fmt,
        "ext": ext,
        "content_type": content_type,
        "byte_size": len(payload),
        "search_id": search_id,
    }
    file_path.write_bytes(payload)
    try:
        meta_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        file_path.unlink(missing_ok=True)
        raise
    logger.info(
        "stage=tts saved audio_id=%s fmt=%s bytes=%d",
        audio_id, fmt, len(payload),
    )
    return StoredTtsAudio(
        audio_id=audio_id,
        created_at=created_at,
        file_path=file_path,
        meta_path=meta_path,
        content_type=content_type,
        byte_size=len(payload),
    )


def get_stored_tts_audio(audio_id: str, *, stage: str = "audio") -> StoredTtsAudio:
    """读取 TTS 音频，不存在或已过期则抛出 AppError 404。"""
    if not TTS_ID_RE.fullmatch(audio_id):
        raise AppError(404, "AUDIO_NOT_FOUND", "音频文件不存在或已过期", stage)

    directory = tts_dir().resolve()
    meta_path = (directory / f"{audio_id}.json").resolve()
    if meta_path.parent != directory or not meta_path.is_file():
        raise AppError(404, "AUDIO_NOT_FOUND", "音频文件不存在或已过期", stage)

    try:
        record = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AppError(404, "AUDIO_NOT_FOUND", "音频文件不存在或已过期", stage) from exc

    raw = record.get("created_at")
    if not raw:
        raise AppError(404, "AUDIO_NOT_FOUND", "音频文件不存在或已过期", stage)
    try:
        created_at = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise AppError(404, "AUDIO_NOT_FOUND", "音频文件不存在或已过期", stage) from exc

    if created_at.tzinfo is None:
        created_at = created_at.astimezone()
    now = datetime.now().astimezone()
    if now - created_at >= timedelta(hours=settings.temp_data_ttl_hours):
        raise AppError(404, "AUDIO_NOT_FOUND", "音频文件不存在或已过期", stage)

    ext = record.get("ext") or _ext_for(record.get("format", "wav"))
    file_path = (directory / f"{audio_id}.{ext}").resolve()
    if file_path.parent != directory or not file_path.is_file():
        raise AppError(404, "AUDIO_NOT_FOUND", "音频文件不存在或已过期", stage)

    return StoredTtsAudio(
        audio_id=audio_id,
        created_at=created_at,
        file_path=file_path,
        meta_path=meta_path,
        content_type=record.get("content_type", "audio/wav"),
        byte_size=int(record.get("byte_size") or file_path.stat().st_size),
    )


def purge_expired_tts_audio() -> int:
    """清理已过期的 TTS 音频文件，返回删除数量。"""
    directory = tts_dir()
    ttl = timedelta(hours=settings.temp_data_ttl_hours)
    now = datetime.now().astimezone()
    removed = 0
    for meta_path in directory.glob("*.json"):
        try:
            record = json.loads(meta_path.read_text(encoding="utf-8"))
            raw = record.get("created_at")
            if not raw:
                continue
            created_at = datetime.fromisoformat(raw)
            if created_at.tzinfo is None:
                created_at = created_at.astimezone()
            if now - created_at < ttl:
                continue
            ext = record.get("ext") or _ext_for(record.get("format", "wav"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        audio_path = meta_path.with_suffix(f".{ext}")
        audio_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        removed += 1
    if removed:
        logger.info("stage=tts purged_expired=%d", removed)
    return removed
