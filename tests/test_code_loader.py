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
