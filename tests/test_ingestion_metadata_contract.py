import shared.database as database_module
from backend.services.ingestion_service import IngestionService
from shared.database import KnowledgeChunk
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker


def test_normalize_chunk_metadata_sets_contract_defaults():
    service = IngestionService(db=None)  # type: ignore[arg-type]

    meta = service._normalize_chunk_metadata(
        base_meta={},
        content="Example page body with two simple tokens.",
        source_type="web",
        source_path="https://example.com/page",
        chunk_title="",
        doc_class="instruction",
        language="en",
        doc_hash="abc123",
        doc_version=2,
        source_updated_at="2026-03-06T00:00:00+00:00",
        chunk_no=3,
    )

    assert meta["type"] == "web"
    assert meta["title"] == "https://example.com/page"
    assert meta["doc_title"] == "https://example.com/page"
    assert meta["section_title"] == "https://example.com/page"
    assert meta["section_path"] == "https://example.com/page"
    assert meta["section_path_norm"] == "https://example.com/page"
    assert meta["chunk_kind"] == "text"
    assert meta["block_type"] == "text"
    assert meta["document_class"] == "instruction"
    assert meta["language"] == "en"
    assert meta["doc_hash"] == "abc123"
    assert meta["doc_version"] == 2
    assert meta["chunk_no"] == 3
    assert len(meta["chunk_hash"]) == 64
    assert meta["token_count_est"] >= 7
    assert meta["parser_profile"] == "loader:web:v1"
    assert meta["source_updated_at"] == "2026-03-06T00:00:00+00:00"


def test_normalize_chunk_metadata_preserves_existing_fields():
    service = IngestionService(db=None)  # type: ignore[arg-type]

    meta = service._normalize_chunk_metadata(
        base_meta={
            "title": "Custom title",
            "doc_title": "Custom doc",
            "section_title": "Section A",
            "section_path": "Custom doc > Section A",
            "section_path_norm": "custom doc > section a",
            "chunk_kind": "code",
            "block_type": "header",
            "type": "markdown",
            "page": 7,
            "char_start": 10,
            "char_end": 50,
            "parser_profile": "loader:markdown:v2",
            "parser_confidence": "0.85",
            "parser_warning": (
                "warning with credential https://user:secret@example.com/private "
                "Authorization: Bearer super-token password=hunter2 token=abc123"
            ),
            "parent_chunk_id": "parent-1",
            "prev_chunk_id": "prev-1",
            "next_chunk_id": "next-1",
        },
        content="print('hello world')",
        source_type="md",
        source_path="docs/a.md",
        chunk_title="Fallback",
        doc_class="reference",
        language="ru",
        doc_hash=None,
        doc_version=1,
        source_updated_at="2026-03-06T00:00:00+00:00",
        chunk_no=9,
    )

    assert meta["type"] == "markdown"
    assert meta["title"] == "Custom title"
    assert meta["doc_title"] == "Custom doc"
    assert meta["section_title"] == "Section A"
    assert meta["section_path"] == "Custom doc > Section A"
    assert meta["section_path_norm"] == "custom doc > section a"
    assert meta["chunk_kind"] == "code"
    assert meta["block_type"] == "header"
    assert "doc_hash" not in meta
    assert meta["document_class"] == "reference"
    assert meta["language"] == "ru"
    assert meta["chunk_no"] == 9
    assert meta["page_no"] == 7
    assert meta["char_start"] == 10
    assert meta["char_end"] == 50
    assert meta["parser_profile"] == "loader:markdown:v2"
    assert meta["parser_confidence"] == 0.85
    assert "***:***@" in meta["parser_warning"]
    assert "secret@" not in meta["parser_warning"]
    assert "Bearer ***" in meta["parser_warning"]
    assert "super-token" not in meta["parser_warning"]
    assert "password=***" in meta["parser_warning"]
    assert "token=***" in meta["parser_warning"]
    assert "hunter2" not in meta["parser_warning"]
    assert "abc123" not in meta["parser_warning"]
    assert meta["parent_chunk_id"] == "parent-1"
    assert meta["prev_chunk_id"] == "prev-1"
    assert meta["next_chunk_id"] == "next-1"


def test_knowledge_chunk_model_exposes_canonical_columns():
    columns = set(KnowledgeChunk.__table__.columns.keys())

    for expected in {
        "chunk_hash",
        "chunk_no",
        "block_type",
        "parent_chunk_id",
        "prev_chunk_id",
        "next_chunk_id",
        "section_path_norm",
        "page_no",
        "char_start",
        "char_end",
        "token_count_est",
        "parser_profile",
        "parser_confidence",
        "parser_warning",
    }:
        assert expected in columns


def test_migrate_database_adds_canonical_chunk_columns(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy_chunks.sqlite3"
    temp_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    temp_session = sessionmaker(bind=temp_engine, expire_on_commit=False)

    with temp_engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE knowledge_chunks (
                id INTEGER PRIMARY KEY,
                knowledge_base_id INTEGER,
                content TEXT,
                metadata TEXT,
                embedding TEXT,
                source_type VARCHAR(50),
                source_path VARCHAR(500),
                created_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            INSERT INTO knowledge_chunks (
                id, knowledge_base_id, content, metadata, embedding, source_type, source_path
            ) VALUES (
                1, 7, 'legacy body', '{"legacy": true}', '[]', 'web', 'https://example.com/wiki'
            )
        """))

    monkeypatch.setattr(database_module, "engine", temp_engine)
    monkeypatch.setattr(database_module, "Session", temp_session)

    database_module.migrate_database()

    inspector = inspect(temp_engine)
    columns = {column["name"] for column in inspector.get_columns("knowledge_chunks")}
    for expected in {
        "chunk_metadata",
        "metadata_json",
        "is_deleted",
        "chunk_hash",
        "chunk_no",
        "block_type",
        "parent_chunk_id",
        "prev_chunk_id",
        "next_chunk_id",
        "section_path_norm",
        "page_no",
        "char_start",
        "char_end",
        "token_count_est",
        "parser_profile",
        "parser_confidence",
        "parser_warning",
    }:
        assert expected in columns

    with temp_engine.connect() as conn:
        row = conn.execute(
            text("SELECT chunk_metadata, metadata_json, is_deleted FROM knowledge_chunks WHERE id = 1")
        ).fetchone()

    assert row is not None
    assert row[0] is not None and '"legacy"' in row[0]
    assert row[1] is None
    assert row[2] == 0
