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
        "get_asr_settings",
        lambda: {"show_asr_metadata": False},
    )
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
        def __init__(self):
            self.edits = []

        async def edit_text(self, text, **kwargs):
            self.edits.append(text)

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
            self.status_messages = []

        async def reply_text(self, text, **kwargs):
            self.sent.append(text)
            status = DummyStatusMessage()
            self.status_messages.append(status)
            return status

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
            self.user_data = {}

    update = DummyUpdate()
    context = DummyContext()

    await bot_handlers.handle_audio(update, context)

    edits = [
        text
        for status in update.message.status_messages
        for text in status.edits
    ]
    assert any("Транскрипция" in text for text in edits)


@pytest.mark.anyio
async def test_handle_audio_transcription_sent_to_ai_in_ai_mode(monkeypatch):
    from frontend import bot_handlers

    async def _check_user(_update):
        return UserContext(
            telegram_id="1",
            username="user",
            full_name=None,
            role="user",
            approved=True,
            preferred_provider="prov",
            preferred_model="model",
        )

    monkeypatch.setattr(bot_handlers, "check_user", _check_user)
    monkeypatch.setattr(
        bot_handlers.backend_client,
        "get_asr_settings",
        lambda: {"show_asr_metadata": False},
    )
    monkeypatch.setattr(
        bot_handlers.backend_client,
        "asr_transcribe",
        lambda **kwargs: {"job_id": "job-audio-2", "queue_position": 1},
    )
    monkeypatch.setattr(
        bot_handlers.backend_client,
        "asr_job_status",
        lambda _job_id: {"status": "done", "text": "audio to text"},
    )

    async def _fast_sleep(_seconds):
        return None

    monkeypatch.setattr(bot_handlers.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(
        bot_handlers,
        "create_prompt_with_language",
        lambda text, _ctx, task=None: f"PROMPT::{text}::{task}",
    )

    ai_calls = []

    def _ai_query(prompt, provider_name=None, model=None):
        ai_calls.append((prompt, provider_name, model))
        return "ai audio answer"

    monkeypatch.setattr(bot_handlers.ai_manager, "query", _ai_query)

    class DummyStatusMessage:
        def __init__(self):
            self.edits = []

        async def edit_text(self, text, **kwargs):
            self.edits.append(text)

    class DummyAudio:
        file_id = "audio-2"
        file_name = "ask.mp3"
        file_size = 2048
        mime_type = "audio/mpeg"
        duration = 10

    class DummyMessage:
        def __init__(self):
            self.audio = DummyAudio()
            self.document = None
            self.message_id = 43
            self.date = datetime.now(timezone.utc)
            self.sent = []
            self.status_messages = []

        async def reply_text(self, text, **kwargs):
            self.sent.append(text)
            status = DummyStatusMessage()
            self.status_messages.append(status)
            return status

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
            self.user_data = {"state": "waiting_ai_query"}

    update = DummyUpdate()
    context = DummyContext()

    await bot_handlers.handle_audio(update, context)

    assert ai_calls == [("PROMPT::audio to text::answer", "prov", "model")]
    assert context.user_data["state"] is None
    assert any("🤖 <b>Ответ:</b>" in text for text in update.message.sent)
