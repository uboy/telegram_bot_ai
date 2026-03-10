import sys
import types

from shared.document_loaders.web_loader import WebLoader


def test_web_loader_sets_doc_section_path_and_chunk_numbers(monkeypatch):
    class _FakeResponse:
        content = b"<html></html>"

        def raise_for_status(self):
            return None

    class _FakeTag:
        def __init__(self, text: str):
            self._text = text

        def get_text(self):
            return self._text

        def decompose(self):
            return None

        def __str__(self):
            return self._text

    class _FakeSoup:
        def __init__(self, _content, _parser):
            self.title = types.SimpleNamespace(string="Docs Portal")
            self._main = _FakeTag("<main>content</main>")

        def __call__(self, _tags):
            return []

        def find(self, tag_name):
            if tag_name == "main":
                return self._main
            if tag_name == "h1":
                return _FakeTag("Welcome")
            if tag_name == "body":
                return self._main
            return None

    class _FakeHtml2Text:
        ignore_links = False
        ignore_images = False
        body_width = 0

        def handle(self, _html):
            return "# Welcome\n\n## Setup\n\nRun repo sync.\n\n### Flags\n\n- Use -c\n"

    monkeypatch.setitem(sys.modules, "bs4", types.SimpleNamespace(BeautifulSoup=_FakeSoup))
    monkeypatch.setitem(sys.modules, "html2text", types.SimpleNamespace(HTML2Text=lambda: _FakeHtml2Text()))
    monkeypatch.setattr("shared.document_loaders.web_loader.requests.get", lambda *_args, **_kwargs: _FakeResponse())

    loader = WebLoader()
    chunks = loader.load(
        "https://example.org/docs/setup",
        options={"chunking_mode": "section", "max_chars": 200, "overlap": 0},
    )

    assert len(chunks) >= 2
    assert [chunk["metadata"]["chunk_no"] for chunk in chunks] == list(range(1, len(chunks) + 1))
    assert all(chunk["metadata"]["type"] == "web" for chunk in chunks)
    assert all(chunk["metadata"]["doc_title"] == "Docs Portal" for chunk in chunks)
    assert all(chunk["metadata"]["parser_profile"] == "loader:web:v1" for chunk in chunks)
    assert any(chunk["metadata"]["section_path"] == "Docs Portal > Welcome > Setup" for chunk in chunks)
    assert any(chunk["metadata"]["chunk_kind"] == "list" for chunk in chunks)

