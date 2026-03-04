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
    monkeypatch.setattr(bot_handlers, "get_recent_active_conversation", lambda _uid: None)

    class DummyConv:
        id = 101

    monkeypatch.setattr(bot_handlers, "create_conversation", lambda *args, **kwargs: DummyConv())

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
    assert context.user_data["ai_conversation_id"] == 101
    assert update.message.sent[-1] == "🤖 Задайте вопрос ИИ:"


@pytest.mark.anyio
async def test_handle_text_ask_ai_button_offers_restore_when_conversation_exists(monkeypatch):
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

    class DummyConv:
        id = 55

    monkeypatch.setattr(bot_handlers, "get_recent_active_conversation", lambda _uid: DummyConv())

    class DummyMessage:
        def __init__(self):
            self.text = "🤖 Задать вопрос ИИ"
            self.date = datetime.now(timezone.utc)
            self.sent = []
            self.kwargs = []

        async def reply_text(self, text, **kwargs):
            self.sent.append(text)
            self.kwargs.append(kwargs)

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

    assert context.user_data["state"] == "waiting_ai_resume_choice"
    assert context.user_data["pending_ai_restore_id"] == 55
    assert "Найден предыдущий диалог" in update.message.sent[-1]
    assert "reply_markup" in update.message.kwargs[-1]


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

    async def _render_ai_answer_html_with_context(**kwargs):
        return "🤖 <b>Ответ:</b>\n\n" + ("очень длинный текст " * 700)

    monkeypatch.setattr(
        bot_handlers,
        "_render_ai_answer_html_with_context",
        _render_ai_answer_html_with_context,
    )

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
            self.user_data = {"state": "waiting_ai_query", "ai_conversation_id": 1}

    update = DummyUpdate()
    context = DummyContext()

    await bot_handlers.handle_text(update, context)

    assert context.user_data["state"] == "waiting_ai_query"
    assert len(update.message.sent) >= 2
    assert all(len(chunk) <= 4096 for chunk in update.message.sent)


@pytest.mark.anyio
async def test_handle_text_waiting_kb_name_creates_knowledge_base(monkeypatch):
    from frontend import bot_handlers

    async def _check_user(_update):
        return UserContext(
            telegram_id="1",
            username="admin",
            full_name=None,
            role="admin",
            approved=True,
        )

    monkeypatch.setattr(bot_handlers, "check_user", _check_user)
    calls = []

    def _create_knowledge_base(name):
        calls.append(name)
        return {"id": 777, "name": name}

    monkeypatch.setattr(bot_handlers.backend_client, "create_knowledge_base", _create_knowledge_base)

    class DummyMessage:
        def __init__(self):
            self.text = "MVP"
            self.date = datetime.now(timezone.utc)
            self.sent = []
            self.kwargs = []

        async def reply_text(self, text, **kwargs):
            self.sent.append(text)
            self.kwargs.append(kwargs)

    class DummyUser:
        id = 1

    class DummyUpdate:
        def __init__(self):
            self.message = DummyMessage()
            self.effective_user = DummyUser()

    class DummyContext:
        def __init__(self):
            self.user_data = {"state": "waiting_kb_name"}

    update = DummyUpdate()
    context = DummyContext()

    await bot_handlers.handle_text(update, context)

    assert calls == ["MVP"]
    assert context.user_data["state"] is None
    assert update.message.sent[-1] == "✅ База знаний 'MVP' создана!"
    assert "reply_markup" in update.message.kwargs[-1]
