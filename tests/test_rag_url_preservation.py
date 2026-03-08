import pytest

pytest.importorskip("fastapi")

from backend.api.routes.rag import rag_query
from backend.schemas.rag import RAGQuery
from shared.rag_safety import strip_untrusted_urls


GITEE_WIKI_URL = "https://gitee.com/mazurdenis/open-harmony/wikis/Sync&Build/Sync%26Build"


class DummyKB:
    settings = {
        "rag": {
            "single_page_mode": True,
            "single_page_top_k": 1,
            "full_page_context_multiplier": 5,
        }
    }


class DummyQuery:
    def __init__(self, rows=None):
        self._rows = rows or []
        self._limit = None

    def filter_by(self, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self._limit = value
        return self

    def all(self):
        rows = list(self._rows)
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    def first(self):
        if self._rows:
            return self._rows[0]
        return DummyKB()


class DummyDB:
    def query(self, _model):
        return DummyQuery(rows=[DummyKB()])


def test_strip_untrusted_urls_preserves_grounded_gitee_url_from_allowlist():
    context = ""
    answer = f"See [wiki]({GITEE_WIKI_URL}) and {GITEE_WIKI_URL}"

    cleaned = strip_untrusted_urls(answer, context, allowed_urls=[GITEE_WIKI_URL])

    assert f"[wiki]({GITEE_WIKI_URL})" in cleaned
    assert cleaned.count(GITEE_WIKI_URL) == 2


def test_strip_untrusted_urls_still_strips_unlisted_url_with_allowlist_present():
    context = "CONTENT: Use the wiki page for sync and build instructions."
    answer = f"See [wiki]({GITEE_WIKI_URL}) and https://example.com/unsafe"

    cleaned = strip_untrusted_urls(answer, context, allowed_urls=[GITEE_WIKI_URL])

    assert f"[wiki]({GITEE_WIKI_URL})" in cleaned
    assert "https://example.com/unsafe" not in cleaned


def test_rag_query_preserves_grounded_source_url_and_strips_untrusted_url(monkeypatch):
    from backend.api.routes import rag as rag_module

    def fake_search(query=None, knowledge_base_id=None, top_k=8, **_kwargs):
        assert query == "Where is the sync and build guide?"
        assert knowledge_base_id == 1
        return [
            {
                "id": 101,
                "content": "Use the Sync&Build wiki page for the complete procedure.",
                "metadata": {"section_title": "Sync&Build", "section_path": "Sync&Build"},
                "source_path": GITEE_WIKI_URL,
                "source_type": "wiki",
                "rerank_score": 0.93,
                "distance": 0.07,
            }
        ]

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(fake_search)})())
    monkeypatch.setattr(
        rag_module,
        "ai_manager",
        type(
            "Y",
            (),
            {
                "query": staticmethod(
                    lambda _prompt: f"See [source wiki]({GITEE_WIKI_URL}) and https://example.com/unsafe"
                )
            },
        )(),
    )

    payload = RAGQuery(query="Where is the sync and build guide?", knowledge_base_id=1)
    result = rag_query(payload, db=DummyDB())

    assert f"[source wiki]({GITEE_WIKI_URL})" in result.answer
    assert "https://example.com/unsafe" not in result.answer
    assert result.sources[0].source_path == GITEE_WIKI_URL
