from datetime import datetime, timezone

import pytest

pytest.importorskip("telegram")

from shared.types import UserContext


@pytest.mark.anyio
async def test_handle_text_ask_ai_button_enters_ai_mode(monkeypatch):
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

    class DummyMessage:
        def __init__(self):
            self.text = "🤖 Задать вопрос ИИ"
            self.date = datetime.now(timezone.utc)
            self.sent = []

        async def reply_text(self, text, **kwargs):
            self.sent.append(text)

    class DummyUser:
        id = 1

    class DummyUpdate:
        def __init__(self):
            self.message = DummyMessage()
            self.effective_user = DummyUser()

    class DummyContext:
        def __init__(self):
            self.user_data = {}

    update = DummyUpdate()
    context = DummyContext()

    await bot_handlers.handle_text(update, context)

    assert context.user_data["state"] == "waiting_ai_query"
    assert update.message.sent[-1] == "🤖 Задайте вопрос ИИ:"


@pytest.mark.anyio
async def test_handle_text_waiting_ai_query_empty_input(monkeypatch):
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

    class DummyMessage:
        def __init__(self):
            self.text = "   "
            self.date = datetime.now(timezone.utc)
            self.sent = []

        async def reply_text(self, text, **kwargs):
            self.sent.append(text)

    class DummyUser:
        id = 1

    class DummyUpdate:
        def __init__(self):
            self.message = DummyMessage()
            self.effective_user = DummyUser()

    class DummyContext:
        def __init__(self):
            self.user_data = {"state": "waiting_ai_query"}

    update = DummyUpdate()
    context = DummyContext()

    await bot_handlers.handle_text(update, context)

    assert context.user_data["state"] == "waiting_ai_query"
    assert update.message.sent[-1] == "⚠️ Введите непустой вопрос для ИИ."


@pytest.mark.anyio
async def test_handle_text_waiting_ai_query_long_answer_split(monkeypatch):
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

    async def _render_ai_answer_html(_query, _user):
        return "🤖 <b>Ответ:</b>\n\n" + ("очень длинный текст " * 700)

    monkeypatch.setattr(bot_handlers, "render_ai_answer_html", _render_ai_answer_html)

    class DummyMessage:
        def __init__(self):
            self.text = "тест"
            self.date = datetime.now(timezone.utc)
            self.sent = []

        async def reply_text(self, text, **kwargs):
            if len(text) > 4096:
                raise RuntimeError("Message is too long")
            self.sent.append(text)

    class DummyUser:
        id = 1

    class DummyUpdate:
        def __init__(self):
            self.message = DummyMessage()
            self.effective_user = DummyUser()

    class DummyContext:
        def __init__(self):
            self.user_data = {"state": "waiting_ai_query"}

    update = DummyUpdate()
    context = DummyContext()

    await bot_handlers.handle_text(update, context)

    assert context.user_data["state"] is None
    assert len(update.message.sent) >= 2
    assert all(len(chunk) <= 4096 for chunk in update.message.sent)
