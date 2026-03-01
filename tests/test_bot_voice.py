import pytest
from datetime import datetime, timezone

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
        "get_asr_settings",
        lambda: {"show_asr_metadata": False},
    )

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
        file_size = 1024
        mime_type = "audio/ogg"

    class DummyMessage:
        def __init__(self):
            self.voice = DummyVoice()
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

    await bot_handlers.handle_voice(update, context)

    edits = [
        text
        for status in update.message.status_messages
        for text in status.edits
    ]
    assert any("Транскрипция" in text for text in edits)


@pytest.mark.anyio
async def test_handle_voice_transcription_sent_to_ai_in_ai_mode(monkeypatch):
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
        lambda **kwargs: {"job_id": "job-2", "queue_position": 1},
    )
    monkeypatch.setattr(
        bot_handlers.backend_client,
        "asr_job_status",
        lambda _job_id: {"status": "done", "text": "voice to text"},
    )

    async def _fast_sleep(_seconds):
        return None

    monkeypatch.setattr(bot_handlers.asyncio, "sleep", _fast_sleep)
    calls = []

    async def _render_ai_answer_html_with_context(**kwargs):
        calls.append(kwargs)
        return "🤖 <b>Ответ:</b>\n\nai voice answer"

    monkeypatch.setattr(
        bot_handlers,
        "_render_ai_answer_html_with_context",
        _render_ai_answer_html_with_context,
    )

    class DummyStatusMessage:
        def __init__(self):
            self.edits = []

        async def edit_text(self, text, **kwargs):
            self.edits.append(text)

    class DummyVoice:
        file_id = "voice-2"
        file_size = 1024
        mime_type = "audio/ogg"

    class DummyMessage:
        def __init__(self):
            self.voice = DummyVoice()
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
            self.user_data = {"state": "waiting_ai_query", "ai_conversation_id": 99}

    update = DummyUpdate()
    context = DummyContext()

    await bot_handlers.handle_voice(update, context)

    assert len(calls) == 1
    assert calls[0]["query"] == "voice to text"
    assert calls[0]["feature"] == "ask_ai_voice"
    assert context.user_data["state"] == "waiting_ai_query"
    assert any("🤖 <b>Ответ:</b>" in text for text in update.message.sent)
