from pathlib import Path

from backend.services.ingestion_service import IngestionService


class DummyDB:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1


def test_enqueue_index_upsert_event_skips_empty_chunks(monkeypatch):
    service = IngestionService(db=object())
    calls = []

    monkeypatch.setattr(
        "backend.services.ingestion_service.index_outbox_service.enqueue_event",
        lambda **kwargs: calls.append(kwargs),
    )

    service._enqueue_index_upsert_event(
        kb_id=1,
        document_id=10,
        version=2,
        source_type="document",
        source_path="doc.md",
        chunks_added=0,
    )

    assert calls == []


def test_enqueue_index_upsert_event_sends_payload(monkeypatch):
    service = IngestionService(db=object())
    calls = []

    monkeypatch.setattr(
        "backend.services.ingestion_service.index_outbox_service.enqueue_event",
        lambda **kwargs: calls.append(kwargs),
    )

    service._enqueue_index_upsert_event(
        kb_id=7,
        document_id=701,
        version=3,
        source_type="web",
        source_path="https://example.org/page",
        chunks_added=42,
    )

    assert len(calls) == 1
    payload = calls[0]["payload"]
    assert calls[0]["operation"] == "UPSERT"
    assert calls[0]["knowledge_base_id"] == 7
    assert calls[0]["document_id"] == 701
    assert calls[0]["version"] == 3
    assert payload["source_type"] == "web"
    assert payload["source_path"] == "https://example.org/page"
    assert payload["chunks_added"] == 42
    assert "updated_at" in payload


def test_ingest_web_page_emits_web_outbox_event(monkeypatch):
    db = DummyDB()
    service = IngestionService(db=db)
    captured = []

    monkeypatch.setattr(service, "_get_kb_settings", lambda _kb_id: {})
    monkeypatch.setattr(service, "_classify_from_chunks", lambda _chunks, _path: "text")
    monkeypatch.setattr(service, "_infer_language_from_chunks", lambda _chunks: "ru")
    monkeypatch.setattr(service, "_upsert_document", lambda **kwargs: (11, 2))
    monkeypatch.setattr(
        "backend.services.ingestion_service.document_loader_manager.load_document",
        lambda *_args, **_kwargs: [{"content": "hello world", "metadata": {}}],
    )
    monkeypatch.setattr("backend.services.ingestion_service.rag_system.delete_chunks_by_source_exact", lambda **_kwargs: 0)
    monkeypatch.setattr("backend.services.ingestion_service.rag_system.add_chunks_batch", lambda _rows: [])
    monkeypatch.setattr(service, "_enqueue_index_upsert_event", lambda **kwargs: captured.append(kwargs))

    result = service.ingest_web_page(
        kb_id=3,
        url="https://example.org/page",
        telegram_id="42",
        username="tester",
    )

    assert result["chunks_added"] == 1
    assert len(captured) == 1
    assert captured[0]["source_type"] == "web"
    assert captured[0]["source_path"] == "https://example.org/page"
    assert captured[0]["chunks_added"] == 1
    assert captured[0]["document_id"] == 11
    assert captured[0]["version"] == 2


def test_ingest_web_page_normalizes_chunk_metadata(monkeypatch):
    db = DummyDB()
    service = IngestionService(db=db)
    captured_rows = []

    monkeypatch.setattr(service, "_get_kb_settings", lambda _kb_id: {})
    monkeypatch.setattr(service, "_classify_from_chunks", lambda _chunks, _path: "instruction")
    monkeypatch.setattr(service, "_infer_language_from_chunks", lambda _chunks: "en")
    monkeypatch.setattr(service, "_upsert_document", lambda **kwargs: (77, 4))
    monkeypatch.setattr(
        "backend.services.ingestion_service.document_loader_manager.load_document",
        lambda *_args, **_kwargs: [{"content": "run build", "metadata": {"type": "web"}}],
    )
    monkeypatch.setattr("backend.services.ingestion_service.rag_system.delete_chunks_by_source_exact", lambda **_kwargs: 0)

    def _capture_batch(rows):
        captured_rows.extend(rows)
        return []

    monkeypatch.setattr("backend.services.ingestion_service.rag_system.add_chunks_batch", _capture_batch)
    monkeypatch.setattr(service, "_enqueue_index_upsert_event", lambda **_kwargs: None)

    service.ingest_web_page(
        kb_id=9,
        url="https://example.org/wiki",
        telegram_id="42",
        username="tester",
    )

    assert len(captured_rows) == 1
    meta = captured_rows[0]["metadata"]
    assert meta["type"] == "web"
    assert meta["title"] == "https://example.org/wiki"
    assert meta["doc_title"] == "https://example.org/wiki"
    assert meta["section_title"] == "https://example.org/wiki"
    assert meta["section_path"] == "https://example.org/wiki"
    assert meta["chunk_kind"] == "text"
    assert meta["block_type"] == "text"
    assert meta["document_class"] == "instruction"
    assert meta["language"] == "en"
    assert meta["doc_version"] == 4
    assert meta["chunk_no"] == 1
    assert len(meta["chunk_hash"]) == 64
    assert meta["parser_profile"] == "loader:web:v1"
    assert meta["section_path_norm"] == "https://example.org/wiki"
    assert "source_updated_at" in meta
    assert captured_rows[0]["metadata_json"] == meta
    assert captured_rows[0]["chunk_columns"]["chunk_hash"] == meta["chunk_hash"]
    assert captured_rows[0]["chunk_columns"]["chunk_no"] == 1
    assert captured_rows[0]["chunk_columns"]["block_type"] == "text"
    assert captured_rows[0]["chunk_columns"]["section_path_norm"] == "https://example.org/wiki"


def test_ingest_codebase_path_emits_code_and_codebase_events(monkeypatch, tmp_path: Path):
    db = DummyDB()
    service = IngestionService(db=db)
    captured = []

    code_file = tmp_path / "main.py"
    code_file.write_text("print('ok')\n", encoding="utf-8")

    monkeypatch.setattr(service, "_get_kb_settings", lambda _kb_id: {})
    monkeypatch.setattr(service, "_get_existing_doc_hash", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_classify_from_chunks", lambda _chunks, _path: "code")
    monkeypatch.setattr(service, "_infer_language_from_chunks", lambda _chunks: "en")
    monkeypatch.setattr(service, "_upsert_document", lambda **kwargs: (22, 5))
    monkeypatch.setattr(
        "backend.services.ingestion_service.document_loader_manager.load_document",
        lambda *_args, **_kwargs: [{"content": "print('ok')", "metadata": {}}],
    )
    monkeypatch.setattr("backend.services.ingestion_service.rag_system.delete_chunks_by_source_exact", lambda **_kwargs: 0)
    monkeypatch.setattr("backend.services.ingestion_service.rag_system.add_chunks_batch", lambda _rows: [])
    monkeypatch.setattr(service, "_enqueue_index_upsert_event", lambda **kwargs: captured.append(kwargs))

    result = service.ingest_codebase_path(
        kb_id=7,
        code_path=str(tmp_path),
        telegram_id="42",
        username="tester",
        repo_label="repo",
    )

    assert result["chunks_added"] == 1
    assert len(captured) == 2

    per_file_event = captured[0]
    aggregate_event = captured[1]

    assert per_file_event["source_type"] == "code"
    assert per_file_event["document_id"] == 22
    assert per_file_event["version"] == 5

    assert aggregate_event["source_type"] == "codebase"
    assert aggregate_event["source_path"] == "repo"
    assert aggregate_event["document_id"] is None
    assert aggregate_event["version"] is None
    assert aggregate_event["chunks_added"] == 1


def test_ingest_codebase_path_sets_code_metadata_contract(monkeypatch, tmp_path: Path):
    db = DummyDB()
    service = IngestionService(db=db)
    captured_rows = []

    code_file = tmp_path / "main.py"
    code_file.write_text("print('ok')\n", encoding="utf-8")

    monkeypatch.setattr(service, "_get_kb_settings", lambda _kb_id: {})
    monkeypatch.setattr(service, "_get_existing_doc_hash", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_classify_from_chunks", lambda _chunks, _path: "code")
    monkeypatch.setattr(service, "_infer_language_from_chunks", lambda _chunks: "en")
    monkeypatch.setattr(service, "_upsert_document", lambda **kwargs: (33, 7))
    monkeypatch.setattr(
        "backend.services.ingestion_service.document_loader_manager.load_document",
        lambda *_args, **_kwargs: [{"content": "print('ok')", "metadata": {}}],
    )
    monkeypatch.setattr("backend.services.ingestion_service.rag_system.delete_chunks_by_source_exact", lambda **_kwargs: 0)

    def _capture_batch(rows):
        captured_rows.extend(rows)
        return []

    monkeypatch.setattr("backend.services.ingestion_service.rag_system.add_chunks_batch", _capture_batch)
    monkeypatch.setattr(service, "_enqueue_index_upsert_event", lambda **_kwargs: None)

    service.ingest_codebase_path(
        kb_id=2,
        code_path=str(tmp_path),
        telegram_id="42",
        username="tester",
        repo_label="repo",
    )

    assert len(captured_rows) == 1
    meta = captured_rows[0]["metadata"]
    assert meta["type"] == "code"
    assert meta["chunk_kind"] == "code_file"
    assert meta["block_type"] == "code_file"
    assert meta["title"] == "main.py"
    assert meta["doc_title"] == "main.py"
    assert meta["section_title"] == "main.py"
    assert meta["section_path"] == "main.py"
    assert meta["section_path_norm"] == "main.py"
    assert meta["code_lang"] == "python"
    assert meta["file_path"] == "main.py"
    assert meta["repo_root"] == "repo"
    assert meta["doc_version"] == 7
    assert meta["chunk_no"] == 1
    assert len(meta["chunk_hash"]) == 64
    assert meta["parser_profile"] == "loader:code:v1"
    assert captured_rows[0]["metadata_json"] == meta
    assert captured_rows[0]["chunk_columns"]["block_type"] == "code_file"
    assert captured_rows[0]["chunk_columns"]["chunk_no"] == 1
    assert captured_rows[0]["chunk_columns"]["parser_profile"] == "loader:code:v1"
