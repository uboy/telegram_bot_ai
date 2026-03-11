import pytest

pytest.importorskip("fastapi")

from backend.api.routes.rag import rag_query
from backend.schemas.rag import RAGQuery
from shared.rag_system import merge_multi_query_candidates


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
    def __init__(self, chunk_rows=None):
        self._chunk_rows = chunk_rows or []

    def query(self, _model):
        model_name = getattr(_model, "__name__", "")
        if model_name == "KnowledgeBase":
            return DummyQuery(rows=[DummyKB()])
        if model_name == "KnowledgeChunk":
            return DummyQuery(rows=self._chunk_rows)
        return DummyQuery()


def _set_generalized_mode(monkeypatch) -> None:
    import shared.config as shared_config

    monkeypatch.setattr(shared_config, "RAG_ORCHESTRATOR_V4", True, raising=False)
    monkeypatch.setattr(shared_config, "RAG_LEGACY_QUERY_HEURISTICS", True, raising=False)


def _set_legacy_mode(monkeypatch) -> None:
    import shared.config as shared_config

    monkeypatch.setattr(shared_config, "RAG_ORCHESTRATOR_V4", False, raising=False)
    monkeypatch.setattr(shared_config, "RAG_LEGACY_QUERY_HEURISTICS", True, raising=False)


def test_merge_multi_query_candidates_deduplicates_by_identity():
    original = {
        "id": 1,
        "content": "Alpha evidence chunk",
        "metadata": {"doc_title": "Guide", "chunk_no": 1},
        "source_path": "doc://guide",
        "source_type": "markdown",
        "rerank_score": 0.61,
        "query_variant_mode": "original",
        "query_variant_query": "alpha",
        "query_variant_reason": "original",
    }
    rewrite = {
        "id": 1,
        "content": "Alpha evidence chunk",
        "metadata": {"doc_title": "Guide", "chunk_no": 1},
        "source_path": "doc://guide",
        "source_type": "markdown",
        "rerank_score": 0.55,
        "query_variant_mode": "keyword_focus",
        "query_variant_query": "alpha guide",
        "query_variant_reason": "content_terms",
    }

    merged = merge_multi_query_candidates([[original], [rewrite]])

    assert len(merged) == 1
    assert merged[0]["multi_query_hit_count"] == 2
    assert set(merged[0]["query_variant_modes"]) == {"original", "keyword_focus"}
    assert merged[0]["multi_query_score"] > 0.61


def test_controlled_query_variants_stay_bounded(monkeypatch):
    from backend.api.routes import rag as rag_module

    query = "Подскажи как настроить корпоративную почту на ноутбуке"
    variants = rag_module._build_controlled_query_variants(
        query,
        rag_module._extract_query_hints(query),
        max_variants=3,
    )

    assert variants[0]["query"] == query
    assert len(variants) <= 3
    assert len({item["query"].lower() for item in variants}) == len(variants)


def test_rag_query_generalized_mode_uses_rewrite_variant(monkeypatch):
    from backend.api.routes import rag as rag_module

    _set_generalized_mode(monkeypatch)
    search_calls = []

    def fake_search(query, knowledge_base_id=None, top_k=8):
        search_calls.append(query)
        normalized = " ".join(str(query).lower().split())
        if normalized in {"определение разметка данных", "разметка данных"}:
            return [
                {
                    "content": "Разметка данных - этап обработки структурированных и неструктурированных данных.",
                    "metadata": {"section_title": "Термины и определения", "section_path": "пункт 2"},
                    "source_path": "doc://glossary",
                    "source_type": "pdf",
                    "rerank_score": 0.66,
                    "distance": 0.22,
                }
            ]
        return []

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(fake_search)})())
    monkeypatch.setattr(rag_module, "ai_manager", type("Y", (), {"query": staticmethod(lambda prompt: prompt)})())

    payload = RAGQuery(query="Что такое разметка данных?", knowledge_base_id=1)
    result = rag_query(payload, db=DummyDB())

    assert search_calls[0] == "Что такое разметка данных?"
    assert "определение разметка данных" in [q.lower() for q in search_calls]
    assert len(search_calls) <= 3
    assert "Разметка данных - этап обработки" in result.answer


def test_rag_query_legacy_mode_skips_rewrite_fanout(monkeypatch):
    from backend.api.routes import rag as rag_module

    _set_legacy_mode(monkeypatch)
    search_calls = []

    def fake_search(query, knowledge_base_id=None, top_k=8):
        search_calls.append(query)
        return [
            {
                "content": "В документе описаны общие положения и обзор программы.",
                "metadata": {"section_title": "Обзор", "section_path": "пункт 1"},
                "source_path": "doc://policy",
                "source_type": "pdf",
                "rerank_score": 0.72,
                "distance": 0.18,
            }
        ]

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(fake_search)})())
    monkeypatch.setattr(rag_module, "ai_manager", type("Y", (), {"query": staticmethod(lambda prompt: prompt)})())

    payload = RAGQuery(query="Что такое разметка данных?", knowledge_base_id=1)
    result = rag_query(payload, db=DummyDB())

    assert search_calls == ["Что такое разметка данных?"]
    assert "общие положения и обзор программы" in result.answer


def test_rag_query_generalized_mode_skips_rewrite_for_explicit_long_fact_query(monkeypatch):
    from backend.api.routes import rag as rag_module

    _set_generalized_mode(monkeypatch)
    search_calls = []

    def fake_search(query, knowledge_base_id=None, top_k=8):
        search_calls.append(query)
        return [
            {
                "content": "Целевой ежегодный объем услуг установлен на уровне не менее 60 млрд рублей к 2030 году.",
                "metadata": {"section_title": "Целевые показатели", "section_path": "пункт 31"},
                "source_path": "doc://policy",
                "source_type": "pdf",
                "rerank_score": 0.79,
                "distance": 0.12,
            }
        ]

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(fake_search)})())
    monkeypatch.setattr(rag_module, "ai_manager", type("Y", (), {"query": staticmethod(lambda prompt: prompt)})())

    payload = RAGQuery(
        query="Какой целевой ежегодный объем услуг по решениям в области ИИ установлен на 2030 год?",
        knowledge_base_id=1,
    )
    result = rag_query(payload, db=DummyDB())

    assert search_calls == [payload.query]
    assert "не менее 60 млрд рублей к 2030 году" in result.answer


def test_rag_query_generalized_mode_prefers_sync_build_doc_for_broad_build_sync_query(monkeypatch):
    from backend.api.routes import rag as rag_module

    _set_generalized_mode(monkeypatch)

    def fake_search(query, knowledge_base_id=None, top_k=8):  # noqa: ARG001
        return [
            {
                "content": "XTS-specific build flow for hvigor migration.",
                "metadata": {
                    "doc_title": "ArkUI XTS converting to Hvigor. The development process.",
                    "section_title": "ArkUI XTS converting to Hvigor. The development process.",
                    "section_path": "Features/XTS/ArkUI XTS converting to Hvigor. The development process.",
                },
                "source_path": "https://gitee.com/org/repo/wikis/Features/XTS/ArkUI%20XTS%20converting%20to%20Hvigor",
                "source_type": "web",
                "rerank_score": 0.95,
                "distance": 0.05,
            },
            {
                "content": "Initialize repository with repo init, run repo sync, then build/prebuilts_download.sh and ./build.sh.",
                "metadata": {
                    "doc_title": "Sync&Build",
                    "section_title": "Initialize repository and sync code",
                    "section_path": "Sync&Build > Initialize repository and sync code",
                },
                "source_path": "https://gitee.com/org/repo/wikis/Sync%26Build/Sync%26Build",
                "source_type": "web",
                "rerank_score": 0.70,
                "distance": 0.15,
            },
        ]

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(fake_search)})())
    monkeypatch.setattr(rag_module, "ai_manager", type("Y", (), {"query": staticmethod(lambda prompt: prompt)})())

    payload = RAGQuery(query="how to build and sync", knowledge_base_id=1)
    result = rag_query(payload, db=DummyDB())

    assert result.sources
    assert result.sources[0].source_path.endswith("/Sync%26Build/Sync%26Build")
    assert "repo init, run repo sync" in result.answer
