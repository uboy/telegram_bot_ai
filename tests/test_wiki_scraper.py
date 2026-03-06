import pytest


def test_should_use_git_wiki_loader_for_gitee_urls():
    from shared import wiki_scraper

    assert wiki_scraper._should_use_git_wiki_loader("https://gitee.com/org/repo/wikis") is True
    assert wiki_scraper._should_use_git_wiki_loader("https://gitee.com/org/repo/wikis/Section/Page") is True
    assert wiki_scraper._should_use_git_wiki_loader("https://example.com/org/repo/wikis") is False


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
    }
