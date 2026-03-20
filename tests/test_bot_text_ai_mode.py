"""
Tests for Telegram Bot text state machine and AI mode.
"""
import asyncio
import os
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, AsyncMock
from types import SimpleNamespace

from frontend import bot_handlers
from shared.types import UserContext

# Set dummy environment for tests
os.environ["TELEGRAM_BOT_TOKEN"] = "test_token"
os.environ["ADMIN_IDS"] = "1,2,3"


@pytest.mark.anyio
async def test_handle_text_ask_ai_button_enters_ai_mode(monkeypatch):
    async def _check_user(_update):
        return UserContext(
            telegram_id="1",
            username="user",
            full_name="User Name",
            role="user",
            approved=True,
        )

    monkeypatch.setattr(bot_handlers, "check_user", _check_user)

    # Mock conversation service
    monkeypatch.setattr(bot_handlers, "get_recent_active_conversation", lambda _tg_id: None)
    
    mock_conv = SimpleNamespace(id=123)
    monkeypatch.setattr(bot_handlers, "create_conversation", lambda *args, **kwargs: mock_conv)

    class DummyMessage:
        def __init__(self):
            self.text = "🤖 Задать вопрос ИИ"
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
    assert context.user_data["ai_conversation_id"] == 123
    assert "Задайте вопрос ИИ" in update.message.sent[0]


@pytest.mark.anyio
async def test_handle_text_ask_ai_button_offers_restore_when_conversation_exists(monkeypatch):
    async def _check_user(_update):
        return UserContext(
            telegram_id="1",
            username="user",
            full_name="User Name",
            role="user",
            approved=True,
        )

    monkeypatch.setattr(bot_handlers, "check_user", _check_user)

    # Mock existing conversation
    mock_conv = SimpleNamespace(id=456)
    monkeypatch.setattr(bot_handlers, "get_recent_active_conversation", lambda _tg_id: mock_conv)

    class DummyMessage:
        def __init__(self):
            self.text = "🤖 Задать вопрос ИИ"
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

    assert context.user_data["state"] == "waiting_ai_resume_choice"
    assert context.user_data["pending_ai_restore_id"] == 456
    assert "Найден предыдущий диалог" in update.message.sent[0]


@pytest.mark.anyio
async def test_handle_text_waiting_ai_query_empty_input(monkeypatch):
    async def _check_user(_update):
        return UserContext(
            telegram_id="1",
            username="user",
            full_name="User Name",
            role="user",
            approved=True,
        )

    monkeypatch.setattr(bot_handlers, "check_user", _check_user)

    class DummyMessage:
        def __init__(self):
            self.text = "" # Empty
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

    assert "непустой вопрос" in update.message.sent[0]


@pytest.mark.anyio
async def test_handle_text_waiting_ai_query_long_answer_split(monkeypatch):
    async def _check_user(_update):
        return UserContext(
            telegram_id="1",
            username="user",
            full_name="User Name",
            role="user",
            approved=True,
        )

    monkeypatch.setattr(bot_handlers, "check_user", _check_user)

    # Mock AI response with long text
    long_answer = "A" * 5000
    async def _fake_render(*args, **kwargs):
        return f"🤖 <b>Ответ:</b>\n\n{long_answer}"

    monkeypatch.setattr(bot_handlers, "_render_ai_answer_html_with_context", _fake_render)

    class DummyMessage:
        def __init__(self):
            self.text = "Tell me a long story"
            self.sent = []

        async def reply_text(self, text, **kwargs):
            self.sent.append(text)
            return SimpleNamespace()

    class DummyUser:
        id = 1

    class DummyUpdate:
        def __init__(self):
            self.message = DummyMessage()
            self.effective_user = DummyUser()

    class DummyContext:
        def __init__(self):
            self.user_data = {
                "state": "waiting_ai_query",
                "ai_conversation_id": 123
            }

    update = DummyUpdate()
    context = DummyContext()

    await bot_handlers.handle_text(update, context)

    # Should be split into at least 2 messages
    assert len(update.message.sent) >= 2
    assert all(len(m) <= 4096 for m in update.message.sent)


@pytest.mark.anyio
async def test_handle_text_waiting_kb_name_creates_knowledge_base(monkeypatch):
    async def _check_user(_update):
        return UserContext(
            telegram_id="1",
            username="admin",
            full_name="Admin Name",
            role="admin",
            approved=True,
        )

    monkeypatch.setattr(bot_handlers, "check_user", _check_user)

    # Mock backend KB creation
    mock_kb = {"id": 10, "name": "New KB"}
    monkeypatch.setattr(bot_handlers.backend_client, "create_knowledge_base", lambda name: mock_kb)

    class DummyMessage:
        def __init__(self):
            self.text = "My New KB"
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
            self.user_data = {"state": "waiting_kb_name"}

    update = DummyUpdate()
    context = DummyContext()

    await bot_handlers.handle_text(update, context)

    assert context.user_data["state"] is None
    assert "создана" in update.message.sent[0]
    assert "My New KB" in update.message.sent[0]


@pytest.mark.anyio
async def test_handle_text_waiting_wiki_root_ingests_wiki_crawl(monkeypatch):
    async def _check_user(_update):
        return UserContext(
            telegram_id="1",
            username="admin",
            full_name="Admin Name",
            role="admin",
            approved=True,
        )

    monkeypatch.setattr(bot_handlers, "check_user", _check_user)

    # Mock backend wiki crawl
    def _ingest_wiki_crawl(*, kb_id, url, telegram_id=None, username=None):
        return {
            "status": "success",
            "deleted_chunks": 5,
            "pages_processed": 10,
            "chunks_added": 25,
            "wiki_root": url,
            "crawl_mode": "git"
        }

    monkeypatch.setattr(bot_handlers.backend_client, "ingest_wiki_crawl", _ingest_wiki_crawl)

    class DummyMessage:
        def __init__(self):
            self.text = "https://wiki.example.com"
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
            self.user_data = {
                "state": "waiting_wiki_root",
                "kb_id_for_wiki": 42
            }

    update = DummyUpdate()
    context = DummyContext()

    await bot_handlers.handle_text(update, context)

    assert context.user_data["state"] is None
    assert "Сканирование вики завершено" in update.message.sent[0]
    assert "25" in update.message.sent[0] # chunks_added


@pytest.mark.anyio
async def test_handle_text_waiting_wiki_root_keeps_recovery_state_on_failed_ingest(monkeypatch):
    from frontend import bot_handlers

    async def _check_user(_update):
        return UserContext(
            telegram_id="1",
            username="admin",
            full_name="Admin Name",
            role="admin",
            approved=True,
        )

    monkeypatch.setattr(bot_handlers, "check_user", _check_user)

    def _ingest_wiki_crawl(*, kb_id, url, telegram_id=None, username=None):
        return {
            "status": "failed",
            "stage": "validation",
            "failure_reason": "git_auth_required",
            "failure_message": "Need auth",
            "recovery_options": ["provide_auth", "upload_wiki_zip"],
            "deleted_chunks": 0,
            "pages_processed": 1,
            "chunks_added": 4,
            "wiki_root": url,
            "crawl_mode": "html",
            "git_fallback_attempted": True,
        }

    monkeypatch.setattr(bot_handlers.backend_client, "ingest_wiki_crawl", _ingest_wiki_crawl)

    class DummyMessage:
        def __init__(self):
            self.text = "https://gitee.com/mazurdenis/open-harmony/wikis"
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
            self.user_data = {
                "state": "waiting_wiki_root",
                "kb_id_for_wiki": 42,
                "pending_documents": [{"file_name": "stale.pdf"}],
            }

    update = DummyUpdate()
    context = DummyContext()

    await bot_handlers.handle_text(update, context)

    # New behavior: if provide_auth is in recovery_options, switch to waiting_wiki_git_token
    assert context.user_data["state"] == "waiting_wiki_git_token"
    assert "Git-репозиторий wiki требует авторизации" in update.message.sent[0]


@pytest.mark.anyio
async def test_handle_document_waiting_wiki_archive_uses_wiki_zip_restore(monkeypatch):
    async def _check_user(_update):
        return UserContext(
            telegram_id="1",
            username="admin",
            full_name="Admin Name",
            role="admin",
            approved=True,
        )

    monkeypatch.setattr(bot_handlers, "check_user", _check_user)

    # Mock backend wiki zip ingest
    def _ingest_wiki_zip(*, kb_id, url, zip_bytes, filename, telegram_id=None, username=None):
        return {"files_processed": 15, "chunks_added": 45}

    monkeypatch.setattr(bot_handlers.backend_client, "ingest_wiki_zip", _ingest_wiki_zip)

    class DummyFile:
        async def download_as_bytearray(self):
            return bytearray(b"zip_content")

    class DummyBot:
        async def get_file(self, file_id):
            return DummyFile()

    class DummyMessage:
        def __init__(self):
            self.document = SimpleNamespace(file_id="zip123", file_name="wiki.zip", file_size=100, mime_type="application/zip")
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
        def __init__(self, bot):
            self.bot = bot
            self.user_data = {
                "state": "waiting_wiki_archive",
                "wiki_zip_kb_id": 42,
                "wiki_zip_url": "https://wiki.example.com"
            }

    bot = DummyBot()
    update = DummyUpdate()
    context = DummyContext(bot)

    await bot_handlers.handle_document(update, context)

    assert context.user_data["state"] is None
    assert "Восстановление wiki из ZIP завершено" in update.message.sent[0]
    assert "45" in update.message.sent[0]


@pytest.mark.anyio
async def test_run_rag_query_with_progress_shows_and_deletes(monkeypatch):
    from frontend import bot_handlers

    async def _fake_rag(*, query, kb_id, user, filters=None):
        await asyncio.sleep(0.95)
        return "<b>ok</b>", True, "test_req_id"

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
        full_name="User Name",
        role="user",
        approved=True,
    )
    msg = DummyMessage()

    html, has_answer, req_id = await bot_handlers._run_rag_query_with_progress(
        message=msg,
        query="test",
        kb_id=1,
        user=user,
        filters={},
    )

    assert html == "<b>ok</b>"
    assert has_answer is True
    assert req_id == "test_req_id"
    assert msg.progress.deleted is True


@pytest.mark.anyio
async def test_process_kb_query_queue_preserves_order(monkeypatch):
    from frontend import bot_handlers

    async def _fake_run(*, message, query, kb_id, user, filters=None):
        return f"<b>{query}</b>", True, f"req_{query}"

    sent = []

    class DummySentMessage:
        def __init__(self, message_id):
            self.message_id = message_id

    async def _fake_reply_text(text, **kwargs):
        # Store what was sent and to which message
        sent.append((text, kwargs.get('reply_to_message_id')))
        return DummySentMessage(999) # Dummy sent message object

    monkeypatch.setattr(bot_handlers, "_run_rag_query_with_progress", _fake_run)

    class DummyMessage:
        def __init__(self, msg_id):
            self.message_id = msg_id
            self.reply_text = AsyncMock(side_effect=_fake_reply_text)
    
    class DummyContext:
        def __init__(self):
            self.user_data = {}

    user = UserContext(
        telegram_id="1",
        username="user",
        full_name="User Name",
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

    assert len(sent) == 2
    assert sent[0] == ("<b>first</b>", 101)
    assert sent[1] == ("<b>second</b>", 102)


@pytest.mark.anyio
async def test_handle_text_waiting_query_queues_when_kb_selected(monkeypatch):
    async def _check_user(_update):
        return UserContext(
            telegram_id="1",
            username="user",
            full_name="User Name",
            role="user",
            approved=True,
        )

    monkeypatch.setattr(bot_handlers, "check_user", _check_user)

    class DummyMessage:
        def __init__(self):
            self.text = "my question"
            self.message_id = 555
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
            self.user_data = {
                "state": "waiting_query",
                "active_search_kb_id": 42
            }

    update = DummyUpdate()
    context = DummyContext()

    await bot_handlers.handle_text(update, context)

    queue = context.user_data.get("kb_query_queue", [])
    assert len(queue) == 1
    assert queue[0]["query"] == "my question"
    assert queue[0]["kb_id"] == 42


@pytest.mark.anyio
async def test_handle_text_search_mode_resets_stale_kb_queue(monkeypatch):
    from frontend import bot_handlers

    async def _check_user(_update):
        return UserContext(
            telegram_id="1", 
            username="user", 
            full_name="User Name", 
            role="user", 
            approved=True
        )

    monkeypatch.setattr(bot_handlers, "check_user", _check_user)
    monkeypatch.setattr(bot_handlers.backend_client, "list_knowledge_bases", lambda: [{"id": 1, "name": "KB"}])

    class DummyMessage:
        def __init__(self):
            self.text = "🔍 Поиск в базе знаний"
            self.sent = []
        async def reply_text(self, text, **kwargs):
            self.sent.append(text)

    ctx = SimpleNamespace(user_data={
        "kb_query_queue": [{"stale": True}],
        "kb_query_session_id": 10
    })
    update = SimpleNamespace(message=DummyMessage(), effective_user=SimpleNamespace(id=1))

    await bot_handlers.handle_text(update, ctx)

    assert len(ctx.user_data["kb_query_queue"]) == 0
    assert ctx.user_data["kb_query_session_id"] == 11


@pytest.mark.anyio
async def test_handle_text_search_mode_autoselects_single_kb(monkeypatch):
    from frontend import bot_handlers

    async def _check_user(_update):
        return UserContext(
            telegram_id="1", 
            username="user", 
            full_name="User Name", 
            role="user", 
            approved=True
        )

    monkeypatch.setattr(bot_handlers, "check_user", _check_user)
    # Return exactly one KB
    monkeypatch.setattr(bot_handlers.backend_client, "list_knowledge_bases", lambda: [{"id": 7, "name": "Only One"}])

    msg = SimpleNamespace(text="🔍 Поиск в базе знаний", sent=[], reply_text=AsyncMock())
    ctx = SimpleNamespace(user_data={})
    update = SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=1))

    await bot_handlers.handle_text(update, ctx)

    assert ctx.user_data["active_search_kb_id"] == 7
    assert ctx.user_data["state"] == "waiting_query"
    args, kwargs = msg.reply_text.call_args
    assert "Only One" in args[0]


@pytest.mark.anyio
async def test_handle_text_waiting_kb_for_query_uses_search_only_menu(monkeypatch):
    from frontend import bot_handlers

    async def _check_user(_update):
        return UserContext(
            telegram_id="1", 
            username="user", 
            full_name="User Name", 
            role="user", 
            approved=True
        )

    monkeypatch.setattr(bot_handlers, "check_user", _check_user)
    monkeypatch.setattr(bot_handlers.backend_client, "list_knowledge_bases", lambda: [{"id": 1, "name": "KB1"}, {"id": 2, "name": "KB2"}])

    msg = SimpleNamespace(text="My Question", sent=[], reply_text=AsyncMock())
    ctx = SimpleNamespace(user_data={"state": "waiting_kb_for_query"})
    update = SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=1))

    await bot_handlers.handle_text(update, ctx)

    # Check that pending query was added
    assert len(ctx.user_data["pending_queries"]) == 1
    assert ctx.user_data["pending_queries"][0]["query"] == "My Question"
    
    # Check that knowledge_base_search_menu was called (indirectly via reply_text kwargs)
    args, kwargs = msg.reply_text.call_args
    assert "reply_markup" in kwargs


@pytest.mark.anyio
async def test_handle_text_without_search_state_ignores_admin_kb_id_for_free_text(monkeypatch):
    from frontend import bot_handlers

    async def _check_user(_update):
        # Non-admin user
        return UserContext(
            telegram_id="1", 
            username="user", 
            full_name="User Name", 
            role="user", 
            approved=True
        )

    monkeypatch.setattr(bot_handlers, "check_user", _check_user)

    msg = SimpleNamespace(text="Just some text", reply_text=AsyncMock())
    # User has some kb_id in session (e.g. from previous admin upload attempt that stayed in context)
    # but state is NOT 'waiting_query'
    ctx = SimpleNamespace(user_data={"kb_id": 42, "state": None})
    update = SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=1))

    await bot_handlers.handle_text(update, ctx)

    # Should fall back to handle_start or similar, not trigger KB search
    assert "kb_query_queue" not in ctx.user_data


@pytest.mark.anyio
async def test_enqueue_pending_queries_for_kb_flushes_fifo(monkeypatch):
    from frontend import bot_handlers

    user = UserContext(
        telegram_id="1", 
        username="user", 
        full_name="User Name", 
        role="user", 
        approved=True
    )
    msg1 = SimpleNamespace(message_id=101)
    msg2 = SimpleNamespace(message_id=102)
    
    ctx = SimpleNamespace(user_data={
        "pending_queries": [
            {"query": "first", "message": msg1, "user": user},
            {"query": "second", "message": msg2, "user": user},
        ],
        "kb_query_queue": []
    })

    await bot_handlers.enqueue_pending_queries_for_kb(context=ctx, kb_id=42)

    queue = ctx.user_data["kb_query_queue"]
    assert len(queue) == 2
    assert queue[0]["query"] == "first"
    assert queue[1]["query"] == "second"
    assert "pending_queries" not in ctx.user_data


def test_format_wiki_sync_mode_marks_html_after_git_fallback():
    from frontend import bot_handlers
    
    stats = {"crawl_mode": "html", "git_fallback_attempted": True}
    res = bot_handlers._format_wiki_sync_mode(stats)
    assert "HTML crawl" in res
    assert "после неудачной попытки git fallback" in res
