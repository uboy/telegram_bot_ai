import pytest

pytest.importorskip("telegram")


class _DummyQueryResult:
    def filter_by(self, **kwargs):
        return self

    def first(self):
        return _DummyCachedUser()


class _DummyCachedUser:
    preferred_provider = None
    preferred_model = None
    preferred_image_model = None

    def get(self, key, default=None):
        return getattr(self, key, default)


class _DummySession:
    def query(self, _model):
        return _DummyQueryResult()

    def close(self):
        return None


class _DummyFromUser:
    id = 1
    username = "user"
    full_name = "User"


class _DummyMessage:
    def __init__(self):
        self.texts = []

    async def reply_text(self, text, **kwargs):
        self.texts.append((text, kwargs))


class _DummyQuery:
    def __init__(self, data: str):
        self.data = data
        self.from_user = _DummyFromUser()
        self.message = _DummyMessage()
        self.answers = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


class _DummyUpdate:
    def __init__(self, data: str):
        self.callback_query = _DummyQuery(data)


class _DummyContext:
    def __init__(self):
        self.user_data = {}


@pytest.mark.anyio
async def test_callback_search_kb_prompts_for_choice_when_multiple_kbs(monkeypatch):
    from frontend import bot_callbacks

    monkeypatch.setattr(
        bot_callbacks.backend_client,
        "auth_telegram",
        lambda **kwargs: {"approved": True, "role": "user", "username": "user"},
    )
    monkeypatch.setattr(bot_callbacks, "Session", lambda: _DummySession())
    monkeypatch.setattr(
        bot_callbacks.backend_client,
        "list_knowledge_bases",
        lambda: [{"id": 1, "name": "KB A"}, {"id": 2, "name": "KB B"}],
    )

    edited = {}

    async def _safe_edit_message_text(query, text, reply_markup=None, parse_mode=None):
        edited["text"] = text
        edited["reply_markup"] = reply_markup

    monkeypatch.setattr(bot_callbacks, "safe_edit_message_text", _safe_edit_message_text)

    update = _DummyUpdate("search_kb")
    context = _DummyContext()
    context.user_data = {"active_search_kb_id": 99}

    await bot_callbacks.callback_handler(update, context)

    assert context.user_data["state"] == "waiting_kb_for_query"
    assert context.user_data.get("active_search_kb_id") is None
    assert edited["text"] == "📚 Выберите базу знаний для поиска:"
    callbacks = [button.callback_data for row in edited["reply_markup"].inline_keyboard for button in row]
    assert "kb_select:1" in callbacks
    assert "kb_select:2" in callbacks
    assert "kb_create" not in callbacks
    assert "admin_kb" not in callbacks
    assert "main_menu" in callbacks


@pytest.mark.anyio
async def test_callback_kb_select_sets_search_scope_for_manual_search_choice(monkeypatch):
    from frontend import bot_callbacks

    monkeypatch.setattr(
        bot_callbacks.backend_client,
        "auth_telegram",
        lambda **kwargs: {"approved": True, "role": "user", "username": "user"},
    )
    monkeypatch.setattr(bot_callbacks, "Session", lambda: _DummySession())
    monkeypatch.setattr(
        bot_callbacks.backend_client,
        "list_knowledge_bases",
        lambda: [{"id": 42, "name": "Docs KB", "description": "Docs"}],
    )
    monkeypatch.setattr(bot_callbacks.backend_client, "list_knowledge_sources", lambda _kb_id: [])

    edited = {}

    async def _safe_edit_message_text(query, text, reply_markup=None, parse_mode=None):
        edited["text"] = text
        edited["reply_markup"] = reply_markup

    monkeypatch.setattr(bot_callbacks, "safe_edit_message_text", _safe_edit_message_text)

    update = _DummyUpdate("kb_select:42")
    context = _DummyContext()
    context.user_data = {"state": "waiting_kb_for_query"}

    await bot_callbacks.callback_handler(update, context)

    assert context.user_data["state"] == "waiting_query"
    assert context.user_data["active_search_kb_id"] == 42
    assert context.user_data.get("kb_id") is None
    assert edited["text"] == "✅ Для поиска выбрана база знаний 'Docs KB'.\n🔍 Введите запрос для поиска:"
    assert edited["reply_markup"] is None


@pytest.mark.anyio
async def test_callback_kb_select_flushes_pending_search_queries_to_active_scope(monkeypatch):
    from frontend import bot_callbacks
    from frontend import bot_handlers

    monkeypatch.setattr(
        bot_callbacks.backend_client,
        "auth_telegram",
        lambda **kwargs: {"approved": True, "role": "user", "username": "user"},
    )
    monkeypatch.setattr(bot_callbacks, "Session", lambda: _DummySession())
    monkeypatch.setattr(
        bot_callbacks.backend_client,
        "list_knowledge_bases",
        lambda: [{"id": 7, "name": "Search KB", "description": "Docs"}],
    )
    monkeypatch.setattr(bot_callbacks.backend_client, "list_knowledge_sources", lambda _kb_id: [])

    async def _enqueue_pending_queries_for_kb(**kwargs):
        return 2

    monkeypatch.setattr(bot_handlers, "enqueue_pending_queries_for_kb", _enqueue_pending_queries_for_kb)

    edited = {}

    async def _safe_edit_message_text(query, text, reply_markup=None, parse_mode=None):
        edited["text"] = text
        edited["reply_markup"] = reply_markup

    monkeypatch.setattr(bot_callbacks, "safe_edit_message_text", _safe_edit_message_text)

    update = _DummyUpdate("kb_select:7")
    context = _DummyContext()
    context.user_data = {
        "state": "waiting_kb_for_query",
        "pending_queries": [{"query": "Что нового?"}],
    }

    await bot_callbacks.callback_handler(update, context)

    assert context.user_data["state"] == "waiting_query"
    assert context.user_data["active_search_kb_id"] == 7
    assert edited["text"] == "✅ Для поиска выбрана база знаний 'Search KB'. Запросов в очереди: 2."
    assert edited["reply_markup"] is None


@pytest.mark.anyio
async def test_callback_admin_kb_clears_search_selection_before_admin_kb_select(monkeypatch):
    from frontend import bot_callbacks

    monkeypatch.setattr(
        bot_callbacks.backend_client,
        "auth_telegram",
        lambda **kwargs: {"approved": True, "role": "admin", "username": "admin"},
    )
    monkeypatch.setattr(bot_callbacks, "Session", lambda: _DummySession())
    monkeypatch.setattr(
        bot_callbacks.backend_client,
        "list_knowledge_bases",
        lambda: [{"id": 42, "name": "Docs KB", "description": "Docs"}],
    )
    monkeypatch.setattr(
        bot_callbacks.backend_client,
        "list_knowledge_sources",
        lambda _kb_id: [{"chunks_count": 3}],
    )

    edited = []

    async def _safe_edit_message_text(query, text, reply_markup=None, parse_mode=None):
        edited.append((text, reply_markup))

    monkeypatch.setattr(bot_callbacks, "safe_edit_message_text", _safe_edit_message_text)

    context = _DummyContext()
    context.user_data = {"state": "waiting_kb_for_query", "pending_queries": [{"query": "how to build"}]}

    await bot_callbacks.callback_handler(_DummyUpdate("admin_kb"), context)
    assert context.user_data.get("state") is None
    assert context.user_data.get("pending_queries") is None

    await bot_callbacks.callback_handler(_DummyUpdate("kb_select:42"), context)

    assert edited[-1][0] == "📚 База знаний: Docs KB\n\nОписание: Docs\nФрагментов: 3"
