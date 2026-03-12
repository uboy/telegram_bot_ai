import pytest


def test_should_use_git_wiki_loader_for_gitee_urls():
    from shared import wiki_scraper

    assert wiki_scraper._should_use_git_wiki_loader("https://gitee.com/org/repo/wikis") is True
    assert wiki_scraper._should_use_git_wiki_loader("https://gitee.com/org/repo/wikis/Section/Page") is True
    assert wiki_scraper._should_use_git_wiki_loader("https://example.com/org/repo/wikis") is False


def test_extract_repo_info_from_wiki_url_prefers_public_wiki_git_candidates():
    from shared import wiki_git_loader

    info = wiki_git_loader._extract_repo_info_from_wiki_url("https://gitee.com/mazurdenis/open-harmony/wikis")

    assert info is not None
    assert info["git_url"] == "https://gitee.com/mazurdenis/open-harmony.wiki.git"
    assert info["git_urls"] == [
        "https://gitee.com/mazurdenis/open-harmony.wiki.git",
        "https://gitee.com/mazurdenis/open-harmony.wikis.git",
        "https://gitee.com/mazurdenis/open-harmony/wikis.git",
        "https://gitee.com/mazurdenis/open-harmony.git",
    ]


def test_extract_repo_info_from_git_clone_url():
    """Передача git clone URL (.wiki.git) должна давать тот же результат, что и web URL."""
    from shared import wiki_git_loader

    for git_url in [
        "https://gitee.com/mazurdenis/open-harmony.wiki.git",
        "https://gitee.com/mazurdenis/open-harmony.wikis.git",
    ]:
        info = wiki_git_loader._extract_repo_info_from_wiki_url(git_url)
        assert info is not None, f"Не удалось разобрать URL: {git_url}"
        assert info["owner"] == "mazurdenis"
        assert info["repo"] == "open-harmony"
        assert info["wiki_root"] == "https://gitee.com/mazurdenis/open-harmony/wikis"
        assert info["git_url"] == "https://gitee.com/mazurdenis/open-harmony.wiki.git"


def test_normalize_base_url_converts_git_clone_url():
    """_normalize_base_url должен конвертировать .wiki.git URL в web wiki URL."""
    from shared.wiki_scraper import _normalize_base_url

    assert _normalize_base_url("https://gitee.com/mazurdenis/open-harmony.wiki.git") == \
        "https://gitee.com/mazurdenis/open-harmony/wikis"
    assert _normalize_base_url("https://gitee.com/mazurdenis/open-harmony.wikis.git") == \
        "https://gitee.com/mazurdenis/open-harmony/wikis"
    # Обычный web URL не должен меняться
    assert _normalize_base_url("https://gitee.com/mazurdenis/open-harmony/wikis") == \
        "https://gitee.com/mazurdenis/open-harmony/wikis"


def test_clone_wiki_repo_disables_interactive_prompts(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from shared import wiki_git_loader

    captured = {}

    def _fake_run(args, capture_output, text, timeout, env):  # noqa: ANN001
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["env"] = env
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(wiki_git_loader.subprocess, "run", _fake_run)

    repo_path, err = wiki_git_loader._clone_wiki_repo("https://gitee.com/org/repo.wiki.git", str(tmp_path))

    assert repo_path == str(tmp_path / "wiki_repo")
    assert err == ""
    assert captured["args"] == ["git", "clone", "--depth", "1", "https://gitee.com/org/repo.wiki.git", str(tmp_path / "wiki_repo")]
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert captured["env"]["GCM_INTERACTIVE"] == "Never"
    assert captured["env"]["GIT_ASKPASS"] == "echo"
    assert captured["env"]["SSH_ASKPASS"] == "echo"


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

    assert result["deleted_chunks"] == 2
    assert result["pages_processed"] == 2
    assert result["chunks_added"] == 2
    assert result["wiki_root"] == "https://gitee.com/mazurdenis/open-harmony/wikis"
    assert result["crawl_mode"] == "html"
    assert result["git_fallback_attempted"] is True
    assert result["status"] == "success"
    assert result["stage"] == "html"
    assert result["failure_reason"] is None
    assert result["recovery_options"] == []
    assert result["git_failure_reason"] == "git_clone_failed"
    assert len(added_chunks) == 2


def test_crawl_wiki_to_kb_marks_root_only_gitee_html_fallback_as_failed(monkeypatch):
    from shared import wiki_scraper
    from shared import wiki_git_loader

    class _DummyResponse:
        def __init__(self, html: str):
            self.content = html.encode("utf-8")

        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        wiki_git_loader,
        "load_wiki_from_git",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("fatal: could not read Username for 'https://gitee.com'")),
    )
    monkeypatch.setattr(
        wiki_scraper.rag_system,
        "delete_chunks_by_source_prefix",
        lambda **_kwargs: 0,
    )
    monkeypatch.setattr(
        wiki_scraper.document_loader_manager,
        "load_document",
        lambda *_args, **_kwargs: [{"content": "root only content", "metadata": {}}],
    )
    monkeypatch.setattr(
        wiki_scraper.rag_system,
        "add_chunk",
        lambda **_kwargs: None,
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

    assert result["status"] == "failed"
    assert result["stage"] == "validation"
    assert result["failure_reason"] == "git_auth_required"
    assert result["recovery_options"] == ["provide_auth", "upload_wiki_zip"]


def test_crawl_wiki_to_kb_marks_empty_result_as_failed(monkeypatch):
    from shared import wiki_scraper

    class _DummyResponse:
        def __init__(self, html: str):
            self.content = html.encode("utf-8")

        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        wiki_scraper.rag_system,
        "delete_chunks_by_source_prefix",
        lambda **_kwargs: 0,
    )
    monkeypatch.setattr(
        wiki_scraper.document_loader_manager,
        "load_document",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        wiki_scraper.requests,
        "get",
        lambda *_args, **_kwargs: _DummyResponse("<html><body></body></html>"),
    )

    result = wiki_scraper.crawl_wiki_to_kb(
        base_url="https://example.com/docs/wiki",
        knowledge_base_id=8,
    )

    assert result["status"] == "failed"
    assert result["failure_reason"] == "empty_wiki_result"
    assert result["recovery_options"] == ["retry_git", "upload_wiki_zip"]


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
            "git_url": "https://gitee.com/mazurdenis/open-harmony.wiki.git",
            "git_urls": [
                "https://gitee.com/mazurdenis/open-harmony.wiki.git",
                "https://gitee.com/mazurdenis/open-harmony.git",
            ],
            "wiki_root": "https://gitee.com/mazurdenis/open-harmony/wikis",
        },
    )
    monkeypatch.setattr(
        wiki_git_loader,
        "_clone_wiki_repo",
        lambda *_args, **_kwargs: (str(repo_root), ""),
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


def test_load_wiki_from_git_tries_public_candidates_until_success(monkeypatch, tmp_path):
    from shared import wiki_git_loader

    repo_root = tmp_path / "repo"
    section_dir = repo_root / "Guide"
    section_dir.mkdir(parents=True)
    (section_dir / "Intro.md").write_text("# Intro\nbody\n", encoding="utf-8")
    clone_calls = []

    monkeypatch.setattr(
        wiki_git_loader,
        "_extract_repo_info_from_wiki_url",
        lambda _url: {
            "owner": "mazurdenis",
            "repo": "open-harmony",
            "base_url": "https://gitee.com/mazurdenis/open-harmony",
            "git_url": "https://gitee.com/mazurdenis/open-harmony.wiki.git",
            "git_urls": [
                "https://gitee.com/mazurdenis/open-harmony.wiki.git",
                "https://gitee.com/mazurdenis/open-harmony.wikis.git",
                "https://gitee.com/mazurdenis/open-harmony.git",
            ],
            "wiki_root": "https://gitee.com/mazurdenis/open-harmony/wikis",
        },
    )

    def _fake_clone(git_url, temp_dir, repo_dir_name="wiki_repo"):  # noqa: ANN001
        clone_calls.append((git_url, repo_dir_name))
        if git_url.endswith(".wikis.git"):
            return str(repo_root), ""
        return None, "repository not found"

    monkeypatch.setattr(wiki_git_loader, "_clone_wiki_repo", _fake_clone)
    monkeypatch.setattr(
        wiki_git_loader.rag_system,
        "delete_chunks_by_source_prefix",
        lambda **_kwargs: 0,
    )
    monkeypatch.setattr(
        wiki_git_loader.document_loader_manager,
        "load_document",
        lambda *_args, **_kwargs: [{"content": "alpha chunk body", "metadata": {"section_path": "Heading A"}}],
    )
    monkeypatch.setattr(
        wiki_git_loader.rag_system,
        "add_chunk",
        lambda **_kwargs: None,
    )

    result = wiki_git_loader.load_wiki_from_git(
        wiki_url="https://gitee.com/mazurdenis/open-harmony/wikis",
        knowledge_base_id=15,
    )

    assert result["files_processed"] == 1
    assert clone_calls == [
        ("https://gitee.com/mazurdenis/open-harmony.wiki.git", "wiki_repo_1"),
        ("https://gitee.com/mazurdenis/open-harmony.wikis.git", "wiki_repo_2"),
    ]


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
