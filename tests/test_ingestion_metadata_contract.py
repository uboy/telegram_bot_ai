from backend.services.ingestion_service import IngestionService


def test_normalize_chunk_metadata_sets_contract_defaults():
    service = IngestionService(db=None)  # type: ignore[arg-type]

    meta = service._normalize_chunk_metadata(
        base_meta={},
        source_type="web",
        source_path="https://example.com/page",
        chunk_title="",
        doc_class="instruction",
        language="en",
        doc_hash="abc123",
        doc_version=2,
        source_updated_at="2026-03-06T00:00:00+00:00",
    )

    assert meta["type"] == "web"
    assert meta["title"] == "https://example.com/page"
    assert meta["doc_title"] == "https://example.com/page"
    assert meta["section_title"] == "https://example.com/page"
    assert meta["section_path"] == "https://example.com/page"
    assert meta["chunk_kind"] == "text"
    assert meta["document_class"] == "instruction"
    assert meta["language"] == "en"
    assert meta["doc_hash"] == "abc123"
    assert meta["doc_version"] == 2


def test_normalize_chunk_metadata_preserves_existing_fields():
    service = IngestionService(db=None)  # type: ignore[arg-type]

    meta = service._normalize_chunk_metadata(
        base_meta={
            "title": "Custom title",
            "doc_title": "Custom doc",
            "section_title": "Section A",
            "section_path": "Custom doc > Section A",
            "chunk_kind": "code",
            "type": "markdown",
        },
        source_type="md",
        source_path="docs/a.md",
        chunk_title="Fallback",
        doc_class="reference",
        language="ru",
        doc_hash=None,
        doc_version=1,
        source_updated_at="2026-03-06T00:00:00+00:00",
    )

    assert meta["type"] == "markdown"
    assert meta["title"] == "Custom title"
    assert meta["doc_title"] == "Custom doc"
    assert meta["section_title"] == "Section A"
    assert meta["section_path"] == "Custom doc > Section A"
    assert meta["chunk_kind"] == "code"
    assert "doc_hash" not in meta
    assert meta["document_class"] == "reference"
    assert meta["language"] == "ru"
