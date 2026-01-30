import io
import os

import pytest

pytest.importorskip("fastapi")

from starlette.datastructures import UploadFile

from backend.api.routes import asr as asr_routes


@pytest.mark.anyio
async def test_transcribe_voice_too_large(monkeypatch):
    monkeypatch.setenv("ASR_MAX_FILE_MB", "1")
    data = b"x" * (2 * 1024 * 1024)
    file = UploadFile(filename="voice.ogg", file=io.BytesIO(data), content_type="audio/ogg")

    with pytest.raises(Exception) as excinfo:
        await asr_routes.transcribe_voice(
            file=file,
            telegram_id="1",
            message_id="2",
            language=None,
        )
    assert "audio file is too large" in str(excinfo.value)


@pytest.mark.anyio
async def test_transcribe_voice_queue_full(monkeypatch):
    file = UploadFile(filename="voice.ogg", file=io.BytesIO(b"hello"), content_type="audio/ogg")

    def _enqueue(*args, **kwargs):
        raise RuntimeError("queue is full")

    monkeypatch.setattr(asr_routes, "enqueue_asr_job", _enqueue)

    with pytest.raises(Exception) as excinfo:
        await asr_routes.transcribe_voice(
            file=file,
            telegram_id="1",
            message_id="2",
            language=None,
        )
    assert "queue is full" in str(excinfo.value)


@pytest.mark.anyio
async def test_transcribe_voice_enqueues(monkeypatch):
    file = UploadFile(filename="voice.ogg", file=io.BytesIO(b"hello"), content_type="audio/ogg")

    def _enqueue(file_path, telegram_id, message_id, language=None):
        if os.path.exists(file_path):
            os.remove(file_path)
        return "job-123"

    monkeypatch.setattr(asr_routes, "enqueue_asr_job", _enqueue)

    resp = await asr_routes.transcribe_voice(
        file=file,
        telegram_id="1",
        message_id="2",
        language=None,
    )
    assert resp.job_id == "job-123"
