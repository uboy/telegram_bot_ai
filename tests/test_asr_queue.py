import pytest

from backend.services import asr_queue


def test_asr_queue_backpressure(monkeypatch):
    asr_queue.reset_queue(maxsize=1)
    job_id = asr_queue.enqueue_asr_job("file1.ogg", 1, 1, None, {"original_name": "file1.ogg"})
    assert job_id
    status = asr_queue.get_job_status(job_id)
    assert status is not None
    assert status.status == "queued"
    assert status.audio_meta and status.audio_meta.get("original_name") == "file1.ogg"
    assert status.timing_meta and status.timing_meta.get("queued_at")

    with pytest.raises(RuntimeError):
        asr_queue.enqueue_asr_job("file2.ogg", 1, 2, None)
