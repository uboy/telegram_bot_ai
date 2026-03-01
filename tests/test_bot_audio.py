from datetime import datetime, timezone

import pytest

pytest.importorskip("telegram")

from shared.types import UserContext


@pytest.mark.anyio
async def test_handle_audio_transcription(monkeypatch):
    from frontend import bot_handlers

    async def _check_user(_update):
        return UserContext(
            telegram_id="1",
            username="user",
            full_name=None,
            role="user",
            approved=True,
        )

    monkeypatch.setattr(bot_handlers, "check_user", _check_user)
    monkeypatch.setattr(
        bot_handlers.backend_client,
        "asr_transcribe",
        lambda **kwargs: {"job_id": "job-audio-1", "queue_position": 1},
    )
    monkeypatch.setattr(
        bot_handlers.backend_client,
        "asr_job_status",
        lambda _job_id: {"status": "done", "text": "audio text"},
    )

    async def _fast_sleep(_seconds):
        return None

    monkeypatch.setattr(bot_handlers.asyncio, "sleep", _fast_sleep)

    class DummyStatusMessage:
        async def edit_text(self, text, **kwargs):
            return None

    class DummyAudio:
        file_id = "audio-1"
        file_name = "file.mp3"
        file_size = 2048
        mime_type = "audio/mpeg"
        duration = 10

    class DummyMessage:
        def __init__(self):
            self.audio = DummyAudio()
            self.document = None
            self.message_id = 42
            self.date = datetime.now(timezone.utc)
            self.sent = []

        async def reply_text(self, text, **kwargs):
            self.sent.append(text)
            return DummyStatusMessage()

    class DummyUser:
        id = 1

    class DummyFile:
        async def download_as_bytearray(self):
            return b"audio"

    class DummyBot:
        async def get_file(self, _file_id):
            return DummyFile()

    class DummyUpdate:
        def __init__(self):
            self.message = DummyMessage()
            self.effective_user = DummyUser()

    class DummyContext:
        def __init__(self):
            self.bot = DummyBot()

    update = DummyUpdate()
    context = DummyContext()

    await bot_handlers.handle_audio(update, context)

    assert any("Транскрипция" in text for text in update.message.sent)
