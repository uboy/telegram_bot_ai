import io
import os

import pytest

pytest.importorskip("fastapi")

from starlette.datastructures import Headers, UploadFile

from backend.api.routes import asr as asr_routes


def _audio_upload(filename: str, data: bytes, content_type: str = "audio/ogg") -> UploadFile:
    return UploadFile(
        filename=filename,
        file=io.BytesIO(data),
        headers=Headers({"content-type": content_type}),
    )


@pytest.mark.anyio
async def test_transcribe_voice_too_large(monkeypatch):
    monkeypatch.setenv("ASR_MAX_FILE_MB", "1")
    data = b"x" * (2 * 1024 * 1024)
    file = _audio_upload("voice.ogg", data)

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
    file = _audio_upload("voice.ogg", b"hello")

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
    file = _audio_upload("voice.ogg", b"hello")

    def _enqueue(file_path, telegram_id, message_id, language=None, audio_meta=None):
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


@pytest.mark.anyio
async def test_transcribe_voice_ffmpeg_missing(monkeypatch):
    file = _audio_upload("voice.ogg", b"hello")
    monkeypatch.setattr(asr_routes.shutil, "which", lambda _: None)

    with pytest.raises(Exception) as excinfo:
        await asr_routes.transcribe_voice(
            file=file,
            telegram_id="1",
            message_id="2",
            language=None,
        )
    assert "ffmpeg not found in PATH" in str(excinfo.value)
