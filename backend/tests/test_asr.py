import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from errors import AppError
from main import app
from services import audio_store, bailian_asr


client = TestClient(app)


def _write_stored_audio(root: Path, audio_id: str, created_at: datetime) -> None:
    uploads = root / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / f"{audio_id}.webm").write_bytes(b"fake-webm-bytes")
    (uploads / f"{audio_id}.json").write_text(
        json.dumps(
            {
                "audio_id": audio_id,
                "created_at": created_at.isoformat(),
                "byte_size": 15,
                "duration_sec": 2.4,
                "duration_source": "cluster_timestamps",
            }
        ),
        encoding="utf-8",
    )


def test_asr_missing_field():
    response = client.post("/asr", json={})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["stage"] == "asr"
    assert "request_id" in body


def test_asr_unknown_id():
    response = client.post("/asr", json={"audio_id": "aud_20990101_000000_ffffff"})
    assert response.status_code == 404
    body = response.json()
    assert body["error"] == {
        "code": "AUDIO_NOT_FOUND",
        "message": "音频文件不存在或已过期",
        "stage": "asr",
    }


def test_asr_expired(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_store.settings, "storage_dir", tmp_path)
    audio_id = "aud_20200101_000000_aaaaaa"
    _write_stored_audio(
        tmp_path,
        audio_id,
        datetime.now(timezone.utc) - timedelta(hours=25),
    )
    response = client.post("/asr", json={"audio_id": audio_id})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AUDIO_NOT_FOUND"


def test_asr_success_mocked(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_store.settings, "storage_dir", tmp_path)
    audio_id = "aud_20260906_160000_bbbbbb"
    _write_stored_audio(tmp_path, audio_id, datetime.now().astimezone())
    with patch(
        "api.asr.transcribe_audio",
        new_callable=AsyncMock,
        return_value="我在杭州东站，朋友在西湖龙翔桥地铁站",
    ):
        response = client.post("/asr", json={"audio_id": audio_id})
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["text"] == "我在杭州东站，朋友在西湖龙翔桥地铁站"
    assert "request_id" in body


def test_asr_empty_recognition_mocked(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_store.settings, "storage_dir", tmp_path)
    audio_id = "aud_20260906_160000_cccccc"
    _write_stored_audio(tmp_path, audio_id, datetime.now().astimezone())
    with patch(
        "api.asr.transcribe_audio",
        new_callable=AsyncMock,
        side_effect=AppError(
            422,
            "EMPTY_RECOGNITION",
            "未识别到有效语音内容，请重新录音",
            "asr",
        ),
    ):
        response = client.post("/asr", json={"audio_id": audio_id})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EMPTY_RECOGNITION"


@pytest.mark.asyncio
async def test_transcribe_timeout(monkeypatch):
    monkeypatch.setattr(bailian_asr.settings, "bailian_api_key", "sk-test")

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(bailian_asr.httpx, "AsyncClient", lambda timeout=None: FakeClient())
    with pytest.raises(AppError) as exc:
        await bailian_asr.transcribe_audio(b"webm")
    assert exc.value.status_code == 504
    assert exc.value.code == "ASR_TIMEOUT"


@pytest.mark.asyncio
async def test_transcribe_vendor_error(monkeypatch):
    monkeypatch.setattr(bailian_asr.settings, "bailian_api_key", "sk-test")

    class FakeResponse:
        status_code = 500

        def json(self):
            return {"code": "InternalError"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            assert "audio" not in str(kwargs.get("headers"))
            return FakeResponse()

    monkeypatch.setattr(bailian_asr.httpx, "AsyncClient", lambda timeout=None: FakeClient())
    with pytest.raises(AppError) as exc:
        await bailian_asr.transcribe_audio(b"webm")
    assert exc.value.status_code == 502
    assert exc.value.code == "ASR_SERVICE_ERROR"


@pytest.mark.asyncio
async def test_transcribe_empty_text(monkeypatch):
    monkeypatch.setattr(bailian_asr.settings, "bailian_api_key", "sk-test")

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"output": {"choices": [{"message": {"content": [{"text": "   "}]}}]}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(bailian_asr.httpx, "AsyncClient", lambda timeout=None: FakeClient())
    with pytest.raises(AppError) as exc:
        await bailian_asr.transcribe_audio(b"webm")
    assert exc.value.status_code == 422
    assert exc.value.code == "EMPTY_RECOGNITION"


def test_encode_rejects_oversized_base64(monkeypatch):
    monkeypatch.setattr(bailian_asr.settings, "bailian_asr_encoded_max_bytes", 20)
    with pytest.raises(AppError) as exc:
        bailian_asr.encode_audio_data_uri(b"0123456789")
    assert exc.value.status_code == 413
    assert exc.value.code == "FILE_TOO_LARGE"
