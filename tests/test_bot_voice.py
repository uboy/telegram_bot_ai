import pytest

pytest.importorskip("telegram")

from shared.types import UserContext


@pytest.mark.anyio
async def test_handle_voice_transcription(monkeypatch):
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
        lambda **kwargs: {"job_id": "job-1", "queue_position": 1},
    )
    monkeypatch.setattr(
        bot_handlers.backend_client,
        "asr_job_status",
        lambda _job_id: {"status": "done", "text": "hello"},
    )

    async def _fast_sleep(_seconds):
        return None

    monkeypatch.setattr(bot_handlers.asyncio, "sleep", _fast_sleep)

    class DummyStatusMessage:
        def __init__(self):
            self.edits = []

        async def edit_text(self, text, **kwargs):
            self.edits.append(text)

    class DummyVoice:
        file_id = "voice-1"

    class DummyMessage:
        def __init__(self):
            self.voice = DummyVoice()
            self.message_id = 42
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

    await bot_handlers.handle_voice(update, context)

    assert any("Транскрипция" in text for text in update.message.sent)
