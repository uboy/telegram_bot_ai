import sys
import types
from pathlib import Path

from shared.document_loaders.chunking import split_text_structurally_with_metadata
from shared.document_loaders.pdf_loader import PDFLoader
from shared.document_loaders.word_loader import WordLoader


def test_split_text_structurally_with_metadata_tracks_offsets_and_kind():
    text = "Введение\n\n1. Первый шаг\n2. Второй шаг"

    records = split_text_structurally_with_metadata(text, max_chars=18, overlap=4)

    assert len(records) >= 2
    assert records[0]["char_start"] == 0
    assert records[0]["char_end"] > records[0]["char_start"]
    assert records[0]["chunk_kind"] in {"text", "list"}
    assert records[1]["char_start"] >= records[0]["char_end"]
    assert any(record["chunk_kind"] == "list" for record in records)


def test_pdf_loader_sets_structural_metadata(monkeypatch, tmp_path: Path):
    class _FakePage:
        def __init__(self, text: str):
            self._text = text

        def extract_text(self):
            return self._text

    class _FakePdfReader:
        def __init__(self, _stream):
            self.pages = [
                _FakePage(
                    "I. Общие положения\n"
                    "25. Целями развития являются\n"
                    "условия для роста.\n"
                    "\n"
                    "26. Следующий пункт включает\n"
                    "подробное описание."
                )
            ]

    monkeypatch.setitem(sys.modules, "PyPDF2", types.SimpleNamespace(PdfReader=_FakePdfReader))

    pdf_path = tmp_path / "Strategy.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    loader = PDFLoader()
    chunks = loader.load(str(pdf_path), options={"max_chars": 60, "overlap": 0})

    assert len(chunks) >= 2
    first_meta = chunks[0]["metadata"]
    second_meta = chunks[1]["metadata"]

    assert first_meta["type"] == "pdf"
    assert first_meta["doc_title"] == "Strategy"
    assert first_meta["page"] == 1
    assert first_meta["page_no"] == 1
    assert first_meta["chunk_no"] == 1
    assert first_meta["parser_profile"] == "loader:pdf:v2"
    assert first_meta["section_title"] == "I. Общие положения"
    assert first_meta["section_path"] == "Strategy > I. Общие положения"
    assert first_meta["char_start"] == 0
    assert first_meta["char_end"] > 0

    assert second_meta["chunk_no"] == 2
    assert second_meta["page_chunk_no"] == 2
    assert second_meta["char_start"] >= first_meta["char_end"]
    assert second_meta["section_title"] in {"пункт 25", "пункт 26", "Страница 1"}
    assert second_meta["parser_profile"] == "loader:pdf:v2"


def test_word_loader_preserves_heading_path_and_paragraph_spans(monkeypatch, tmp_path: Path):
    class _FakeStyle:
        def __init__(self, name: str):
            self.name = name

    class _FakeParagraph:
        def __init__(self, text: str, style_name: str):
            self.text = text
            self.style = _FakeStyle(style_name)

    class _FakeDocument:
        def __init__(self, _source):
            self.paragraphs = [
                _FakeParagraph("Раздел 1", "Heading 1"),
                _FakeParagraph("Первый абзац раздела.", "Normal"),
                _FakeParagraph("Первый пункт", "List Bullet"),
                _FakeParagraph("Подраздел", "Heading 2"),
                _FakeParagraph("Второй абзац подраздела.", "Normal"),
            ]

    monkeypatch.setitem(sys.modules, "docx", types.SimpleNamespace(Document=_FakeDocument))

    doc_path = tmp_path / "Policy.docx"
    doc_path.write_bytes(b"fake-docx")

    loader = WordLoader()
    chunks = loader.load(str(doc_path), options={"max_chars": 120, "overlap": 0})

    assert len(chunks) == 2
    first_meta = chunks[0]["metadata"]
    second_meta = chunks[1]["metadata"]

    assert first_meta["type"] == "word"
    assert first_meta["doc_title"] == "Policy"
    assert first_meta["section_title"] == "Раздел 1"
    assert first_meta["section_path"] == "Policy > Раздел 1"
    assert first_meta["heading_level"] == 1
    assert first_meta["chunk_no"] == 1
    assert first_meta["parser_profile"] == "loader:word:v2"
    assert first_meta["paragraph_start"] == 1
    assert first_meta["paragraph_end"] == 3
    assert first_meta["paragraph_count"] == 3
    assert first_meta["char_start"] == 0
    assert first_meta["char_end"] > 0
    assert chunks[0]["content"].startswith("Раздел 1")
    assert "- Первый пункт" in chunks[0]["content"]

    assert second_meta["section_title"] == "Подраздел"
    assert second_meta["section_path"] == "Policy > Раздел 1 > Подраздел"
    assert second_meta["heading_level"] == 2
    assert second_meta["chunk_no"] == 2
    assert second_meta["paragraph_start"] == 4
    assert second_meta["paragraph_end"] == 5
    assert second_meta["char_start"] >= first_meta["char_end"]
