from shared.document_loaders.code_loader import extract_symbols


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
