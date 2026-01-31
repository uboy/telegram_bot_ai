from datetime import datetime, timezone

import pytest

pytest.importorskip("sqlalchemy")

from backend.services import asr_worker
from backend.services.asr_queue import AsrJob


def test_worker_fallback_model(monkeypatch):
    job = AsrJob(
        job_id="job-1",
        file_path="voice.ogg",
        telegram_id=1,
        message_id=2,
        language=None,
        created_at=datetime.now(timezone.utc),
        audio_meta={},
    )

    statuses = []

    def _set_status(job_id, status, text=None, error=None, queue_position=None, audio_meta=None, timing_meta=None):
        statuses.append((status, text, error))

    def _next_job():
        if not getattr(_next_job, "called", False):
            _next_job.called = True
            return job
        raise SystemExit

    def _transcribe(path, language, model_name, device):
        if model_name == "bad-model":
            raise RuntimeError("bad model")
        return "ok"

    monkeypatch.setattr(asr_worker, "set_job_status", _set_status)
    monkeypatch.setattr(asr_worker, "next_job", _next_job)
    monkeypatch.setattr(asr_worker, "mark_done", lambda _job: None)
    monkeypatch.setattr(asr_worker, "_get_settings", lambda: ("transformers", "bad-model", None))
    monkeypatch.setattr(asr_worker, "_ensure_ffmpeg_available", lambda _path: None)
    monkeypatch.setattr(asr_worker, "_log_audio_metadata", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(asr_worker, "_probe_audio_metadata", lambda _path: {})
    monkeypatch.setattr(asr_worker, "_convert_audio_if_needed", lambda path: path)
    monkeypatch.setattr(asr_worker, "_transcribe_audio", _transcribe)

    with pytest.raises(SystemExit):
        asr_worker._worker_loop(1, "cpu")

    assert ("done", "ok", None) in statuses


def test_get_worker_devices_limits():
    assert asr_worker._get_worker_devices(0, None) == ["cpu"]
    assert asr_worker._get_worker_devices(2, None) == ["cuda:0", "cuda:1"]
    assert asr_worker._get_worker_devices(4, 2) == ["cuda:0", "cuda:1"]


def test_ensure_ffmpeg_missing_for_ogg(monkeypatch):
    monkeypatch.setattr(asr_worker.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError):
        asr_worker._ensure_ffmpeg_available("voice.ogg")
