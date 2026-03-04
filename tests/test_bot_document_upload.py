from datetime import datetime, timezone

import pytest

pytest.importorskip("telegram")

from shared.types import UserContext


def test_infer_document_file_type_by_extension_and_mime():
    from frontend import bot_handlers

    assert bot_handlers._infer_document_file_type("notes.md") == "md"
    assert bot_handlers._infer_document_file_type("report.PDF") == "pdf"
    assert bot_handlers._infer_document_file_type("archive.zip") == "zip"
    assert bot_handlers._infer_document_file_type("table.unknown", "text/plain") == "txt"
    assert bot_handlers._infer_document_file_type("binary.unknown", "application/octet-stream") is None


def test_build_document_upload_report_contains_success_and_failure():
    from frontend import bot_handlers

    report = bot_handlers._build_document_upload_report(
        12,
        [
            {"file_name": "a.md", "file_type": "md", "status": "completed"},
            {"file_name": "b.bin", "file_type": "unknown", "status": "failed", "error": "Неподдерживаемый тип файла."},
        ],
    )

    assert "KB #12" in report
    assert "Успешно: 1" in report
    assert "С ошибкой: 1" in report
    assert "a.md (md)" in report
    assert "b.bin (unknown) — Неподдерживаемый тип файла." in report


@pytest.mark.anyio
async def test_handle_document_without_kb_accumulates_pending_queue(monkeypatch):
    from frontend import bot_handlers

    async def _check_user(_update):
        return UserContext(
            telegram_id="7",
            username="admin",
            full_name=None,
            role="admin",
            approved=True,
        )

    monkeypatch.setattr(bot_handlers, "check_user", _check_user)
    monkeypatch.setattr(
        bot_handlers.backend_client,
        "list_knowledge_bases",
        lambda: [{"id": 1, "name": "KB"}],
    )

    class DummyDocument:
        file_id = "f1"
        file_name = "doc1.md"
        file_size = 123
        mime_type = "text/markdown"
        file_unique_id = "u1"

    class DummyMessage:
        def __init__(self):
            self.document = DummyDocument()
            self.text = None
            self.date = datetime.now(timezone.utc)
            self.sent = []
            self.kwargs = []

        async def reply_text(self, text, **kwargs):
            self.sent.append(text)
            self.kwargs.append(kwargs)
            return self

    class DummyChat:
        id = 42

    class DummyUser:
        id = 7

    class DummyUpdate:
        def __init__(self):
            self.message = DummyMessage()
            self.effective_user = DummyUser()
            self.effective_chat = DummyChat()

    class DummyContext:
        def __init__(self):
            self.user_data = {}
            self.application = type("A", (), {"bot_data": {}})()

    update = DummyUpdate()
    context = DummyContext()

    await bot_handlers.handle_document(update, context)
    await bot_handlers.handle_document(update, context)

    assert len(context.user_data["pending_documents"]) == 2
    assert "Файл добавлен в очередь (1 шт.)" in update.message.sent[0]
    assert "Файл добавлен в очередь (2 шт.)" in update.message.sent[1]
    assert "reply_markup" in update.message.kwargs[-1]


@pytest.mark.anyio
async def test_process_document_batch_upload_reports_results(monkeypatch):
    from frontend import bot_handlers

    async def _fake_ingest_single_document_payload(*, context, user, kb_id, payload):
        if payload.get("file_name") == "ok.md":
            return {"file_name": "ok.md", "file_type": "md", "status": "completed"}
        return {
            "file_name": payload.get("file_name"),
            "file_type": "unknown",
            "status": "failed",
            "error": "Неподдерживаемый тип файла.",
        }

    monkeypatch.setattr(bot_handlers, "_ingest_single_document_payload", _fake_ingest_single_document_payload)

    class DummySentMessage:
        async def delete(self):
            return None

    class DummyMessage:
        def __init__(self):
            self.sent = []

        async def reply_text(self, text, **kwargs):
            self.sent.append(text)
            return DummySentMessage()

    class DummyContext:
        def __init__(self):
            self.bot = object()
            self.user_data = {}
            self.application = type("A", (), {"bot_data": {}})()

    msg = DummyMessage()
    ctx = DummyContext()
    user = UserContext(
        telegram_id="7",
        username="admin",
        full_name=None,
        role="admin",
        approved=True,
    )

    await bot_handlers._process_document_batch_upload(
        message=msg,
        context=ctx,
        user=user,
        kb_id=5,
        payloads=[
            {"file_name": "ok.md", "file_id": "1"},
            {"file_name": "bad.bin", "file_id": "2"},
        ],
    )

    combined = "\n".join(msg.sent)
    assert "Получено документов: 2" in combined
    assert "Успешно: 1" in combined
    assert "С ошибкой: 1" in combined
    assert "bad.bin" in combined


@pytest.mark.anyio
async def test_ingest_single_document_payload_calls_backend_ingestion(monkeypatch):
    from frontend import bot_handlers

    class DummyTelegramFile:
        async def download_as_bytearray(self):
            return bytearray(b"hello markdown")

    class DummyBot:
        async def get_file(self, _file_id):
            return DummyTelegramFile()

    class DummyContext:
        def __init__(self):
            self.bot = DummyBot()
            self.application = type("A", (), {"bot_data": {}})()
            self.user_data = {}

    calls = {"ingest": 0, "status": 0}

    def _ingest_document(kb_id, file_name, file_bytes, file_type, telegram_id, username):
        calls["ingest"] += 1
        assert kb_id == 3
        assert file_name == "spec.md"
        assert file_type == "md"
        assert telegram_id == "11"
        assert username == "admin"
        assert file_bytes
        return {"job_id": 901}

    def _get_job_status(job_id):
        calls["status"] += 1
        assert job_id == 901
        return {"status": "completed"}

    monkeypatch.setattr(bot_handlers.backend_client, "ingest_document", _ingest_document)
    monkeypatch.setattr(bot_handlers.backend_client, "get_job_status", _get_job_status)
    monkeypatch.setattr(
        bot_handlers.backend_client,
        "get_import_log",
        lambda _kb_id: [{"source_path": "spec.md", "total_chunks": 17}],
    )

    user = UserContext(
        telegram_id="11",
        username="admin",
        full_name=None,
        role="admin",
        approved=True,
    )

    result = await bot_handlers._ingest_single_document_payload(
        context=DummyContext(),
        user=user,
        kb_id=3,
        payload={
            "file_id": "telegram-file-1",
            "file_name": "spec.md",
            "file_size": 42,
            "mime_type": "text/markdown",
        },
    )

    assert result["status"] == "completed"
    assert result["file_type"] == "md"
    assert result["total_chunks"] == 17
    assert calls["ingest"] == 1
    assert calls["status"] >= 1


@pytest.mark.anyio
async def test_ingest_single_document_payload_reports_missing_job_id(monkeypatch):
    from frontend import bot_handlers

    class DummyTelegramFile:
        async def download_as_bytearray(self):
            return bytearray(b"content")

    class DummyBot:
        async def get_file(self, _file_id):
            return DummyTelegramFile()

    class DummyContext:
        def __init__(self):
            self.bot = DummyBot()
            self.application = type("A", (), {"bot_data": {}})()
            self.user_data = {}

    monkeypatch.setattr(bot_handlers.backend_client, "ingest_document", lambda *args, **kwargs: {})

    user = UserContext(
        telegram_id="11",
        username="admin",
        full_name=None,
        role="admin",
        approved=True,
    )

    result = await bot_handlers._ingest_single_document_payload(
        context=DummyContext(),
        user=user,
        kb_id=3,
        payload={
            "file_id": "telegram-file-2",
            "file_name": "spec.md",
            "file_size": 42,
            "mime_type": "text/markdown",
        },
    )

    assert result["status"] == "failed"
    assert "job_id" in (result.get("error") or "")
