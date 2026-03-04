from datetime import datetime, timezone
import asyncio

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


@pytest.mark.anyio
async def test_run_rag_query_with_progress_shows_and_deletes(monkeypatch):
    from frontend import bot_handlers

    async def _fake_rag(*, query, kb_id, user, filters=None):
        await asyncio.sleep(0.95)
        return "<b>ok</b>", True

    monkeypatch.setattr(bot_handlers, "perform_rag_query_and_render", _fake_rag)
    monkeypatch.setattr(bot_handlers, "AI_PROGRESS_THRESHOLD_SEC", 0.01)

    class DummyProgressMessage:
        def __init__(self):
            self.deleted = False
            self.edits = []

        async def edit_text(self, text):
            self.edits.append(text)

        async def delete(self):
            self.deleted = True

    class DummyMessage:
        def __init__(self):
            self.message_id = 77
            self.sent = []
            self.progress = None

        async def reply_text(self, text, **kwargs):
            self.sent.append((text, kwargs))
            self.progress = DummyProgressMessage()
            return self.progress

    user = UserContext(
        telegram_id="1",
        username="user",
        full_name=None,
        role="user",
        approved=True,
    )
    msg = DummyMessage()

    html, has_answer = await bot_handlers._run_rag_query_with_progress(
        message=msg,
        query="test",
        kb_id=1,
        user=user,
        filters={},
    )

    assert has_answer is True
    assert html == "<b>ok</b>"
    assert msg.sent
    assert "Ищу ответ в базе знаний" in msg.sent[0][0]
    assert msg.progress is not None and msg.progress.deleted is True


@pytest.mark.anyio
async def test_process_kb_query_queue_preserves_order(monkeypatch):
    from frontend import bot_handlers

    async def _fake_run(*, message, query, kb_id, user, filters=None):
        return f"<b>{query}</b>", True

    sent = []

    async def _fake_reply(message, html_text, reply_markup=None, reply_to_message_id=None):
        sent.append((html_text, reply_to_message_id))

    monkeypatch.setattr(bot_handlers, "_run_rag_query_with_progress", _fake_run)
    monkeypatch.setattr(bot_handlers, "reply_html_safe", _fake_reply)

    class DummyMessage:
        def __init__(self, msg_id):
            self.message_id = msg_id

    class DummyContext:
        def __init__(self):
            self.user_data = {}

    user = UserContext(
        telegram_id="1",
        username="user",
        full_name=None,
        role="user",
        approved=True,
    )
    ctx = DummyContext()
    ctx.user_data["kb_query_queue"] = [
        {
            "message": DummyMessage(101),
            "reply_to_message_id": 101,
            "query": "first",
            "kb_id": 1,
            "user": user,
            "filters": {},
        },
        {
            "message": DummyMessage(102),
            "reply_to_message_id": 102,
            "query": "second",
            "kb_id": 1,
            "user": user,
            "filters": {},
        },
    ]

    await bot_handlers._process_kb_query_queue(ctx)

    assert [item[0] for item in sent] == ["<b>first</b>", "<b>second</b>"]
    assert [item[1] for item in sent] == [101, 102]
    assert ctx.user_data.get("kb_query_worker") is None


@pytest.mark.anyio
async def test_handle_text_waiting_query_queues_when_kb_selected(monkeypatch):
    from frontend import bot_handlers

    async def _check_user(_update):
        return UserContext(
            telegram_id="1",
            username="user",
            full_name=None,
            role="user",
            approved=True,
        )

    queue_calls = []

    async def _fake_enqueue(*, context, message, query, kb_id, user, filters=None):
        queue_calls.append((query, kb_id, dict(filters or {})))
        return 1

    monkeypatch.setattr(bot_handlers, "check_user", _check_user)
    monkeypatch.setattr(bot_handlers, "_enqueue_kb_query", _fake_enqueue)

    class DummyMessage:
        def __init__(self):
            self.text = "Что такое ИИ?"
            self.date = datetime.now(timezone.utc)
            self.sent = []

        async def reply_text(self, text, **kwargs):
            self.sent.append((text, kwargs))

    class DummyUser:
        id = 1

    class DummyUpdate:
        def __init__(self):
            self.message = DummyMessage()
            self.effective_user = DummyUser()

    class DummyContext:
        def __init__(self):
            self.user_data = {
                "state": "waiting_query",
                "kb_id": 5,
                "rag_filters": {"source_types": ["pdf"]},
            }

    update = DummyUpdate()
    context = DummyContext()

    await bot_handlers.handle_text(update, context)
    assert context.user_data["state"] == "waiting_query"
    assert queue_calls == [("Что такое ИИ?", 5, {"source_types": ["pdf"]})]


@pytest.mark.anyio
async def test_enqueue_pending_queries_for_kb_flushes_fifo(monkeypatch):
    from frontend import bot_handlers
    queued = []

    async def _fake_enqueue(*, context, message, query, kb_id, user, filters=None):
        queued.append((query, kb_id, dict(filters or {})))
        return len(queued)

    monkeypatch.setattr(bot_handlers, "_enqueue_kb_query", _fake_enqueue)

    class DummyMessage:
        message_id = 501

    class DummyContext:
        def __init__(self):
            self.user_data = {
                "pending_queries": [
                    {"query": "first", "filters": {"source_types": ["pdf"]}},
                    {"query": "second", "filters": {"languages": ["ru"]}},
                ]
            }

    user = UserContext(
        telegram_id="1",
        username="user",
        full_name=None,
        role="user",
        approved=True,
    )
    ctx = DummyContext()
    msg = DummyMessage()

    flushed = await bot_handlers.enqueue_pending_queries_for_kb(
        context=ctx,
        kb_id=9,
        fallback_message=msg,
        fallback_user=user,
    )
    assert flushed == 2
    assert queued == [
        ("first", 9, {"source_types": ["pdf"]}),
        ("second", 9, {"languages": ["ru"]}),
    ]
    assert ctx.user_data.get("pending_queries") is None
