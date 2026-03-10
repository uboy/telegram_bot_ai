import pytest

pytest.importorskip("telegram")


class _DummyQueryResult:
    def filter_by(self, **kwargs):
        return self

    def first(self):
        return None


class _DummySession:
    def query(self, _model):
        return _DummyQueryResult()

    def close(self):
        return None


class _DummyFromUser:
    id = 1
    username = "admin"
    full_name = "Admin"


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
async def test_callback_kb_wiki_crawl_enters_canonical_state_and_clears_legacy_keys(monkeypatch):
    from frontend import bot_callbacks

    monkeypatch.setattr(
        bot_callbacks.backend_client,
        "auth_telegram",
        lambda **kwargs: {"approved": True, "role": "admin", "username": "admin"},
    )
    monkeypatch.setattr(bot_callbacks, "Session", lambda: _DummySession())

    edited = {}

    async def _safe_edit_message_text(query, text, reply_markup=None, parse_mode=None):
        edited["text"] = text
        edited["reply_markup"] = reply_markup

    monkeypatch.setattr(bot_callbacks, "safe_edit_message_text", _safe_edit_message_text)

    update = _DummyUpdate("kb_wiki_crawl:42")
    context = _DummyContext()
    context.user_data = {
        "wiki_urls": {"deadbeef": "https://legacy.example/wiki"},
        "wiki_zip_kb_id": 42,
        "wiki_zip_url": "https://legacy.example/wiki.zip",
        "pending_documents": [{"file_name": "stale.pdf"}],
        "pending_document": {"file_name": "legacy.pdf"},
        "upload_mode": "document_auto",
        "kb_id": 99,
    }

    await bot_callbacks.callback_handler(update, context)

    assert context.user_data["kb_id_for_wiki"] == 42
    assert context.user_data["state"] == "waiting_wiki_root"
    assert context.user_data.get("wiki_urls") is None
    assert context.user_data.get("wiki_zip_kb_id") is None
    assert context.user_data.get("wiki_zip_url") is None
    assert context.user_data.get("pending_documents") is None
    assert context.user_data.get("pending_document") is None
    assert context.user_data.get("upload_mode") is None
    assert context.user_data.get("kb_id") is None
    assert "Введите корневой URL вики" in edited["text"]


@pytest.mark.anyio
async def test_callback_legacy_wiki_buttons_redirect_to_canonical_flow(monkeypatch):
    from frontend import bot_callbacks

    monkeypatch.setattr(
        bot_callbacks.backend_client,
        "auth_telegram",
        lambda **kwargs: {"approved": True, "role": "admin", "username": "admin"},
    )
    monkeypatch.setattr(bot_callbacks, "Session", lambda: _DummySession())

    calls = {"ingest_wiki_git": 0}

    def _ingest_wiki_git(**kwargs):
        calls["ingest_wiki_git"] += 1
        return {}

    monkeypatch.setattr(bot_callbacks.backend_client, "ingest_wiki_git", _ingest_wiki_git)

    edited = {}

    async def _safe_edit_message_text(query, text, reply_markup=None, parse_mode=None):
        edited["text"] = text
        edited["reply_markup"] = reply_markup

    monkeypatch.setattr(bot_callbacks, "safe_edit_message_text", _safe_edit_message_text)

    update = _DummyUpdate("wiki_git_load:42:deadbeef")
    context = _DummyContext()
    context.user_data = {
        "state": "waiting_wiki_zip",
        "wiki_urls": {"deadbeef": "https://legacy.example/wiki"},
        "wiki_zip_kb_id": 42,
        "wiki_zip_url": "https://legacy.example/wiki.zip",
    }

    await bot_callbacks.callback_handler(update, context)

    assert calls["ingest_wiki_git"] == 0
    assert context.user_data["state"] is None
    assert context.user_data.get("wiki_urls") is None
    assert context.user_data.get("wiki_zip_kb_id") is None
    assert context.user_data.get("wiki_zip_url") is None
    assert "устаревший путь загрузки вики" in edited["text"]
    assert "Собрать вики по URL" in edited["text"]


def test_callback_format_wiki_sync_mode_marks_html_after_git_fallback():
    from frontend import bot_callbacks

    assert (
        bot_callbacks._format_wiki_sync_mode(
            {"crawl_mode": "html", "git_fallback_attempted": True}
        )
        == "HTML crawl (после неудачной попытки git fallback)"
    )
