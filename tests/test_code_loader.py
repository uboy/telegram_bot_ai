from shared.document_loaders.code_loader import extract_symbols
from shared.document_loaders.code_loader import CodeLoader


def test_extract_symbols_basic():
    code = """
class Foo:
    pass

def bar():
    return 1

func baz() {}
"""
    symbols = extract_symbols(code)
    assert "Foo" in symbols
    assert "bar" in symbols
    assert "baz" in symbols


def test_code_loader_sets_doc_and_section_metadata(tmp_path):
    p = tmp_path / "example.py"
    p.write_text("def hello():\n    return 1\n", encoding="utf-8")

    loader = CodeLoader()
    chunks = loader.load(str(p), options={"max_chars": 1000})

    assert len(chunks) >= 1
    meta = chunks[0]["metadata"]
    assert meta["type"] == "code"
    assert meta["chunk_kind"] == "code"
    assert meta["doc_title"] == "example"
    assert meta["section_title"] == "example"
    assert meta["section_path"] == "example"
    assert "chunk_no" in meta


def test_code_loader_sets_chunk_level_symbol_paths_and_spans(tmp_path):
    p = tmp_path / "sample.py"
    p.write_text(
        "def alpha():\n"
        "    return 1\n\n"
        "class Beta:\n"
        "    pass\n",
        encoding="utf-8",
    )

    loader = CodeLoader()
    chunks = loader.load(str(p), options={"max_chars": 24, "overlap": 0})

    assert len(chunks) >= 2
    first_meta = chunks[0]["metadata"]
    second_meta = chunks[1]["metadata"]

    assert first_meta["parser_profile"] == "loader:code:v1"
    assert first_meta["file_path"] == "sample.py"
    assert first_meta["section_title"] == "alpha"
    assert first_meta["section_path"] == "sample > alpha"
    assert first_meta["primary_symbol"] == "alpha"
    assert first_meta["chunk_symbols"] == ["alpha"]
    assert first_meta["char_start"] == 0
    assert first_meta["char_end"] > 0

    assert second_meta["section_title"] == "Beta"
    assert second_meta["section_path"] == "sample > Beta"
    assert second_meta["primary_symbol"] == "Beta"
    assert second_meta["char_start"] >= first_meta["char_end"]
    assert second_meta["symbols"] == ["Beta", "alpha"]
