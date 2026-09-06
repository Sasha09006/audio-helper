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

SEARCH_PREFIX = "sch"
SEARCH_ID_RE = re.compile(r"^sch_[0-9]{8}_[0-9]{6}_[0-9a-f]{6}$")


@dataclass(frozen=True)
class StoredSearch:
    search_id: str
    created_at: datetime
    file_path: Path
    record: dict


def searches_dir() -> Path:
    path = settings.storage_dir / "searches"
    path.mkdir(parents=True, exist_ok=True)
    return path


def generate_search_id(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"{SEARCH_PREFIX}_{stamp}_{uuid.uuid4().hex[:6]}"


def save_search(record: dict) -> StoredSearch:
    created_at = datetime.now().astimezone()
    search_id = generate_search_id(created_at)
    payload = {
        **record,
        "search_id": search_id,
        "created_at": created_at.isoformat(),
    }
    file_path = searches_dir() / f"{search_id}.json"
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return StoredSearch(
        search_id=search_id,
        created_at=created_at,
        file_path=file_path,
        record=payload,
    )


def get_stored_search(search_id: str, *, stage: str = "finalize") -> StoredSearch:
    if not SEARCH_ID_RE.fullmatch(search_id):
        raise AppError(404, "SEARCH_NOT_FOUND", "查询结果不存在或已过期", stage)

    directory = searches_dir().resolve()
    file_path = (directory / f"{search_id}.json").resolve()
    if file_path.parent != directory or not file_path.is_file():
        raise AppError(404, "SEARCH_NOT_FOUND", "查询结果不存在或已过期", stage)

    created_at = _created_at_from_file(file_path)
    if created_at is None:
        raise AppError(404, "SEARCH_NOT_FOUND", "查询结果不存在或已过期", stage)
    if created_at.tzinfo is None:
        created_at = created_at.astimezone()
    now = datetime.now().astimezone()
    if now - created_at >= timedelta(hours=settings.temp_data_ttl_hours):
        raise AppError(404, "SEARCH_NOT_FOUND", "查询结果不存在或已过期", stage)

    try:
        record = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AppError(404, "SEARCH_NOT_FOUND", "查询结果不存在或已过期", stage) from exc

    return StoredSearch(
        search_id=search_id,
        created_at=created_at,
        file_path=file_path,
        record=record,
    )


def purge_expired_searches() -> int:
    directory = searches_dir()
    ttl = timedelta(hours=settings.temp_data_ttl_hours)
    now = datetime.now().astimezone()
    removed = 0
    for file_path in directory.glob("*.json"):
        created_at = _created_at_from_file(file_path)
        if created_at is None or now - created_at < ttl:
            continue
        file_path.unlink(missing_ok=True)
        removed += 1
    if removed:
        logger.info("stage=search purged_expired=%s", removed)
    return removed


def _created_at_from_file(file_path: Path) -> datetime | None:
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        raw = payload.get("created_at")
        if not raw:
            return None
        return datetime.fromisoformat(raw)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
