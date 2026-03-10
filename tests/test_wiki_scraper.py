import pytest


def test_should_use_git_wiki_loader_for_gitee_urls():
    from shared import wiki_scraper

    assert wiki_scraper._should_use_git_wiki_loader("https://gitee.com/org/repo/wikis") is True
    assert wiki_scraper._should_use_git_wiki_loader("https://gitee.com/org/repo/wikis/Section/Page") is True
    assert wiki_scraper._should_use_git_wiki_loader("https://example.com/org/repo/wikis") is False


def test_restore_wiki_url_from_path_normalizes_windows_separators():
    from shared import wiki_git_loader

    result = wiki_git_loader._restore_wiki_url_from_path(
        "Guide\\Intro.md",
        "https://gitee.com/org/repo/wikis",
    )

    assert result == "https://gitee.com/org/repo/wikis/Guide/Intro"


def test_crawl_wiki_to_kb_uses_git_loader_for_gitee(monkeypatch):
    from shared import wiki_scraper
    from shared import wiki_git_loader

    calls = []

    def _fake_load_wiki_from_git(*, wiki_url, knowledge_base_id, loader_options=None):
        calls.append(
            {
                "wiki_url": wiki_url,
                "knowledge_base_id": knowledge_base_id,
                "loader_options": loader_options,
            }
        )
        return {
            "deleted_chunks": 4,
            "files_processed": 27,
            "chunks_added": 143,
            "wiki_root": wiki_url,
        }

    def _no_html_requests(*args, **kwargs):
        raise AssertionError("HTML crawl should not run for gitee wiki fallback path")

    monkeypatch.setattr(wiki_git_loader, "load_wiki_from_git", _fake_load_wiki_from_git)
    monkeypatch.setattr(wiki_scraper.requests, "get", _no_html_requests)

    result = wiki_scraper.crawl_wiki_to_kb(
        base_url="https://gitee.com/mazurdenis/open-harmony/wikis",
        knowledge_base_id=9,
        loader_options={"chunk_size": 1000},
    )

    assert calls == [
        {
            "wiki_url": "https://gitee.com/mazurdenis/open-harmony/wikis",
            "knowledge_base_id": 9,
            "loader_options": {"chunk_size": 1000},
        }
    ]
    assert result == {
        "deleted_chunks": 4,
        "pages_processed": 27,
        "chunks_added": 143,
        "wiki_root": "https://gitee.com/mazurdenis/open-harmony/wikis",
        "crawl_mode": "git",
        "git_fallback_attempted": True,
    }


def test_crawl_wiki_to_kb_falls_back_to_html_when_git_loader_fails(monkeypatch):
    from shared import wiki_scraper
    from shared import wiki_git_loader

    def _failing_load_wiki_from_git(*, wiki_url, knowledge_base_id, loader_options=None):
        raise RuntimeError("git unavailable")

    class _DummyResponse:
        def __init__(self, url: str, html: str):
            self.content = html.encode("utf-8")
            self._url = url

        def raise_for_status(self):
            return None

    added_chunks = []

    monkeypatch.setattr(wiki_git_loader, "load_wiki_from_git", _failing_load_wiki_from_git)
    monkeypatch.setattr(
        wiki_scraper.rag_system,
        "delete_chunks_by_source_prefix",
        lambda **kwargs: 2,
    )
    monkeypatch.setattr(
        wiki_scraper.document_loader_manager,
        "load_document",
        lambda *args, **kwargs: [{"content": "hello wiki", "metadata": {}}],
    )
    monkeypatch.setattr(
        wiki_scraper.rag_system,
        "add_chunk",
        lambda **kwargs: added_chunks.append(kwargs),
    )
    monkeypatch.setattr(
        wiki_scraper.requests,
        "get",
        lambda url, timeout, headers: _DummyResponse(
            url,
            '<html><body><a href="/mazurdenis/open-harmony/wikis/Page-2">next</a></body></html>',
        ),
    )

    result = wiki_scraper.crawl_wiki_to_kb(
        base_url="https://gitee.com/mazurdenis/open-harmony/wikis",
        knowledge_base_id=9,
        loader_options={"chunk_size": 1000},
    )

    assert result == {
        "deleted_chunks": 2,
        "pages_processed": 2,
        "chunks_added": 2,
        "wiki_root": "https://gitee.com/mazurdenis/open-harmony/wikis",
        "crawl_mode": "html",
        "git_fallback_attempted": True,
    }
    assert len(added_chunks) == 2


def test_crawl_wiki_to_kb_html_adds_canonical_chunk_payload(monkeypatch):
    from shared import wiki_scraper
    from shared import wiki_git_loader

    class _DummyResponse:
        def __init__(self, html: str):
            self.content = html.encode("utf-8")

        def raise_for_status(self):
            return None

    added_chunks = []

    monkeypatch.setattr(
        wiki_git_loader,
        "load_wiki_from_git",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("git unavailable")),
    )
    monkeypatch.setattr(
        wiki_scraper.rag_system,
        "delete_chunks_by_source_prefix",
        lambda **_kwargs: 0,
    )
    monkeypatch.setattr(
        wiki_scraper.document_loader_manager,
        "load_document",
        lambda *_args, **_kwargs: [
            {"content": "first chunk", "metadata": {}},
            {"content": "second chunk", "metadata": {}},
        ],
    )
    monkeypatch.setattr(
        wiki_scraper.rag_system,
        "add_chunk",
        lambda **kwargs: added_chunks.append(kwargs),
    )
    monkeypatch.setattr(
        wiki_scraper.requests,
        "get",
        lambda *_args, **_kwargs: _DummyResponse("<html><body>root</body></html>"),
    )

    result = wiki_scraper.crawl_wiki_to_kb(
        base_url="https://gitee.com/mazurdenis/open-harmony/wikis",
        knowledge_base_id=5,
    )

    assert result["pages_processed"] == 1
    assert result["chunks_added"] == 2
    assert [item["chunk_no"] for item in added_chunks] == [1, 2]
    assert [item["metadata"]["chunk_no"] for item in added_chunks] == [1, 2]
    assert [item["metadata_json"]["chunk_no"] for item in added_chunks] == [1, 2]
    assert all(item["chunk_columns"]["chunk_no"] in {1, 2} for item in added_chunks)
    assert all(item["chunk_columns"]["parser_profile"] == "loader:web:wiki_html:v1" for item in added_chunks)
    assert added_chunks[0]["metadata"]["chunk_hash"] != added_chunks[1]["metadata"]["chunk_hash"]
    assert added_chunks[0]["chunk_columns"]["chunk_hash"] != added_chunks[1]["chunk_columns"]["chunk_hash"]


def test_load_wiki_from_git_adds_canonical_chunk_payload(monkeypatch, tmp_path):
    from shared import wiki_git_loader

    repo_root = tmp_path / "repo"
    section_dir = repo_root / "Guide"
    section_dir.mkdir(parents=True)
    (section_dir / "Intro.md").write_text("# Intro\nbody\n", encoding="utf-8")
    added_chunks = []

    monkeypatch.setattr(
        wiki_git_loader,
        "_extract_repo_info_from_wiki_url",
        lambda _url: {
            "owner": "mazurdenis",
            "repo": "open-harmony",
            "base_url": "https://gitee.com/mazurdenis/open-harmony",
            "git_url": "https://gitee.com/mazurdenis/open-harmony.git",
            "wiki_root": "https://gitee.com/mazurdenis/open-harmony/wikis",
        },
    )
    monkeypatch.setattr(
        wiki_git_loader,
        "_clone_wiki_repo",
        lambda *_args, **_kwargs: str(repo_root),
    )
    monkeypatch.setattr(
        wiki_git_loader.rag_system,
        "delete_chunks_by_source_prefix",
        lambda **_kwargs: 0,
    )
    monkeypatch.setattr(
        wiki_git_loader.document_loader_manager,
        "load_document",
        lambda *_args, **_kwargs: [
            {"content": "alpha chunk body", "metadata": {"section_path": "Heading A"}},
            {"content": "beta chunk body", "metadata": {"section_path": "Heading B"}},
        ],
    )
    monkeypatch.setattr(
        wiki_git_loader.rag_system,
        "add_chunk",
        lambda **kwargs: added_chunks.append(kwargs),
    )

    result = wiki_git_loader.load_wiki_from_git(
        wiki_url="https://gitee.com/mazurdenis/open-harmony/wikis",
        knowledge_base_id=11,
    )

    assert result["files_processed"] == 1
    assert result["chunks_added"] == 2
    assert [item["chunk_no"] for item in added_chunks] == [1, 2]
    assert [item["metadata"]["chunk_no"] for item in added_chunks] == [1, 2]
    assert [item["metadata_json"]["chunk_no"] for item in added_chunks] == [1, 2]
    assert all(item["chunk_columns"]["parser_profile"] == "loader:web:wiki_git:v1" for item in added_chunks)
    assert added_chunks[0]["metadata"]["file_path"] == "Guide/Intro.md"
    assert added_chunks[0]["metadata"]["wiki_page_path"] == "Guide/Intro"
    assert added_chunks[0]["metadata"]["section_path"] == "Guide/Intro > Heading A"
    assert added_chunks[0]["metadata"]["chunk_hash"] != added_chunks[1]["metadata"]["chunk_hash"]
    assert added_chunks[0]["chunk_columns"]["chunk_hash"] != added_chunks[1]["chunk_columns"]["chunk_hash"]


def test_load_wiki_from_zip_adds_canonical_chunk_payload(monkeypatch, tmp_path):
    import zipfile

    from shared import wiki_git_loader

    zip_path = tmp_path / "wiki.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Guide/Page.md", "# Page\nbody\n")

    added_chunks = []

    monkeypatch.setattr(
        wiki_git_loader.rag_system,
        "delete_chunks_by_source_prefix",
        lambda **_kwargs: 0,
    )
    monkeypatch.setattr(
        wiki_git_loader.document_loader_manager,
        "load_document",
        lambda *_args, **_kwargs: [
            {"content": "zip alpha body", "metadata": {"section_path": "Heading A"}},
            {"content": "zip beta body", "metadata": {"section_path": "Heading B"}},
        ],
    )
    monkeypatch.setattr(
        wiki_git_loader.rag_system,
        "add_chunk",
        lambda **kwargs: added_chunks.append(kwargs),
    )

    result = wiki_git_loader.load_wiki_from_zip(
        zip_path=str(zip_path),
        wiki_url="https://gitee.com/mazurdenis/open-harmony/wikis",
        knowledge_base_id=12,
    )

    assert result["files_processed"] == 1
    assert result["chunks_added"] == 2
    assert [item["chunk_no"] for item in added_chunks] == [1, 2]
    assert [item["metadata"]["chunk_no"] for item in added_chunks] == [1, 2]
    assert [item["metadata_json"]["chunk_no"] for item in added_chunks] == [1, 2]
    assert all(item["chunk_columns"]["parser_profile"] == "loader:web:wiki_zip:v1" for item in added_chunks)
    assert added_chunks[0]["metadata"]["file_path"] == "Guide/Page.md"
    assert added_chunks[0]["metadata"]["wiki_page_path"] == "Guide/Page"
    assert added_chunks[0]["metadata"]["section_path"] == "Guide/Page > Heading A"
    assert added_chunks[0]["metadata"]["chunk_hash"] != added_chunks[1]["metadata"]["chunk_hash"]
    assert added_chunks[0]["chunk_columns"]["chunk_hash"] != added_chunks[1]["chunk_columns"]["chunk_hash"]


def test_load_wiki_from_zip_uses_stable_doc_title_with_real_markdown_loader(monkeypatch, tmp_path):
    import zipfile

    from shared import wiki_git_loader

    zip_path = tmp_path / "wiki-real.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Guide/Page.md", "# Page\n\nBody text for wiki page.\n")

    added_chunks = []

    monkeypatch.setattr(
        wiki_git_loader.rag_system,
        "delete_chunks_by_source_prefix",
        lambda **_kwargs: 0,
    )
    monkeypatch.setattr(
        wiki_git_loader.rag_system,
        "add_chunk",
        lambda **kwargs: added_chunks.append(kwargs),
    )

    result = wiki_git_loader.load_wiki_from_zip(
        zip_path=str(zip_path),
        wiki_url="https://gitee.com/mazurdenis/open-harmony/wikis",
        knowledge_base_id=13,
    )

    assert result["files_processed"] == 1
    assert result["chunks_added"] >= 1
    assert added_chunks
    assert added_chunks[0]["metadata"]["doc_title"] == "Page"
    assert added_chunks[0]["metadata"]["section_title"] == "Page"
    assert added_chunks[0]["metadata"]["file_path"] == "Guide/Page.md"
    assert added_chunks[0]["metadata"]["wiki_page_path"] == "Guide/Page"
    assert added_chunks[0]["metadata"]["section_path"].startswith("Guide/Page")
    assert added_chunks[0]["chunk_title"] == "Page"


def test_load_wiki_from_zip_replaces_temp_titles_in_chunk_metadata(monkeypatch, tmp_path):
    import zipfile

    from shared import wiki_git_loader

    zip_path = tmp_path / "wiki-temp.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Guide/Page.md", "# Page\nbody\n")

    added_chunks = []

    monkeypatch.setattr(
        wiki_git_loader.rag_system,
        "delete_chunks_by_source_prefix",
        lambda **_kwargs: 0,
    )
    monkeypatch.setattr(
        wiki_git_loader.document_loader_manager,
        "load_document",
        lambda *_args, **_kwargs: [
            {
                "content": "body text with enough length",
                "title": "tmpabcd1234",
                "metadata": {"doc_title": "tmpabcd1234", "section_title": "tmpabcd1234"},
            }
        ],
    )
    monkeypatch.setattr(
        wiki_git_loader.rag_system,
        "add_chunk",
        lambda **kwargs: added_chunks.append(kwargs),
    )

    result = wiki_git_loader.load_wiki_from_zip(
        zip_path=str(zip_path),
        wiki_url="https://gitee.com/mazurdenis/open-harmony/wikis",
        knowledge_base_id=14,
    )

    assert result["files_processed"] == 1
    assert result["chunks_added"] == 1
    assert added_chunks[0]["metadata"]["doc_title"] == "Page"
    assert added_chunks[0]["metadata"]["section_title"] == "Page"
    assert added_chunks[0]["chunk_title"] == "Page"
