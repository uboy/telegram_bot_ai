import json
import os
from types import SimpleNamespace

import numpy as np

os.environ["MYSQL_URL"] = ""
os.environ.setdefault("DB_PATH", "data/test-rag-system-budgets.db")

from shared import rag_system as rag_module


class DummyReranker:
    def __init__(self, scores, calls):
        self._scores = list(scores)
        self._calls = calls

    def predict(self, pairs):
        self._calls["rerank_pairs"] = list(pairs)
        return self._scores[: len(pairs)]


def _chunk(content: str, source_path: str, *, metadata: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        chunk_metadata=json.dumps(metadata or {"doc_title": source_path}, ensure_ascii=False),
        source_type="markdown",
        source_path=source_path,
    )


def _candidate(
    content: str,
    source_path: str,
    *,
    distance: float,
    metadata: dict | None = None,
    origin: str = "qdrant",
    source_type: str = "markdown",
):
    return {
        "content": content,
        "metadata": metadata or {},
        "source_type": source_type,
        "source_path": source_path,
        "distance": distance,
        "origin": origin,
    }


def _flatten_unique(ranked_lists):
    ordered = []
    seen = set()
    for ranked in ranked_lists:
        for key in ranked:
            if key in seen:
                continue
            seen.add(key)
            ordered.append(key)
    return ordered


def _set_legacy_heuristics(monkeypatch, *, enabled: bool, orchestrator_v4: bool = False) -> None:
    import shared.config as shared_config

    monkeypatch.setattr(shared_config, "RAG_ORCHESTRATOR_V4", orchestrator_v4, raising=False)
    monkeypatch.setattr(shared_config, "RAG_LEGACY_QUERY_HEURISTICS", enabled, raising=False)


def _build_test_system(
    *,
    dense_budget: int,
    bm25_budget: int,
    rerank_top_n: int,
    rerank_scores,
    is_howto: bool,
    dense_candidates=None,
    bm25_chunks=None,
    bm25_ranked=None,
    field_candidates=None,
    enable_rerank: bool = True,
):
    rag = object.__new__(rag_module.RAGSystem)
    calls = {}

    rag.encoder = object()
    rag.index = None
    rag.chunks = []
    rag.index_by_kb = {}
    rag.chunks_by_kb = {}
    rag.bm25_index_by_kb = {1: {"ready": True}}
    rag.bm25_index_all = None
    rag.bm25_chunks_by_kb = {
        1: list(
            bm25_chunks
            or [
                _chunk("bm25-1", "doc://bm25-1"),
                _chunk("bm25-2", "doc://bm25-2"),
                _chunk("bm25-3", "doc://bm25-3"),
                _chunk("bm25-4", "doc://bm25-4"),
                _chunk("bm25-5", "doc://bm25-5"),
                _chunk("bm25-6", "doc://bm25-6"),
            ]
        )
    }
    rag.bm25_chunks_all = []
    rag.enable_rerank = enable_rerank
    rag.reranker = DummyReranker(rerank_scores, calls) if enable_rerank else None
    rag.max_candidates = max(dense_budget, bm25_budget)
    rag.dense_candidate_budget = dense_budget
    rag.bm25_candidate_budget = bm25_budget
    rag.rerank_top_n = rerank_top_n
    rag._load_index = lambda _knowledge_base_id: None
    rag._get_embedding = lambda _query, is_query=False: np.array([0.1, 0.2], dtype="float32")  # noqa: ARG005
    rag._is_howto_query = lambda _query: is_howto
    rag._qdrant_enabled = lambda: True
    rag._rrf_fuse = _flatten_unique

    def dense_search(*, query_embedding, knowledge_base_id, top_k):  # noqa: ARG001
        calls["dense_top_k"] = top_k
        return list(
            dense_candidates
            or [
                {
                    "content": "dense-1",
                    "metadata": {"doc_title": "Dense 1"},
                    "source_type": "markdown",
                    "source_path": "doc://dense-1",
                    "distance": 0.40,
                    "origin": "qdrant",
                },
                {
                    "content": "dense-2",
                    "metadata": {"doc_title": "Dense 2"},
                    "source_type": "markdown",
                    "source_path": "doc://dense-2",
                    "distance": 0.30,
                    "origin": "qdrant",
                },
                {
                    "content": "dense-3",
                    "metadata": {"doc_title": "Dense 3"},
                    "source_type": "markdown",
                    "source_path": "doc://dense-3",
                    "distance": 0.20,
                    "origin": "qdrant",
                },
            ]
        )[:top_k]

    def bm25_search(_query, _bm25_index, limit):
        calls["bm25_limit"] = limit
        if bm25_ranked is not None:
            return list(bm25_ranked)[:limit]
        return list(range(limit))

    rag._qdrant_dense_search = dense_search
    rag._bm25_search = bm25_search
    rag._metadata_field_search = lambda _query, _chunks, top_k: list(field_candidates or [])[:top_k]
    return rag, calls


def test_search_uses_explicit_channel_budgets_and_rerank_window(monkeypatch):
    _set_legacy_heuristics(monkeypatch, enabled=True)
    monkeypatch.setattr(rag_module, "HAS_EMBEDDINGS", True, raising=False)
    monkeypatch.setattr(rag_module, "HAS_RERANKER", True, raising=False)
    rag, calls = _build_test_system(
        dense_budget=4,
        bm25_budget=2,
        rerank_top_n=2,
        rerank_scores=[0.15, 0.95, 0.50],
        is_howto=True,
    )

    results = rag_module.RAGSystem.search(rag, "how to alpha", knowledge_base_id=1, top_k=1)

    assert calls["dense_top_k"] == 4
    assert calls["bm25_limit"] == 2
    assert len(calls["rerank_pairs"]) == 2
    assert [pair[1] for pair in calls["rerank_pairs"]] == ["dense-1", "dense-2"]
    assert results[0]["content"] == "dense-2"
    assert results[0]["origin"] == "qdrant"


def test_search_rerank_window_never_drops_below_requested_top_k(monkeypatch):
    _set_legacy_heuristics(monkeypatch, enabled=False)
    monkeypatch.setattr(rag_module, "HAS_EMBEDDINGS", True, raising=False)
    monkeypatch.setattr(rag_module, "HAS_RERANKER", True, raising=False)
    rag, calls = _build_test_system(
        dense_budget=5,
        bm25_budget=3,
        rerank_top_n=1,
        rerank_scores=[0.40, 0.90, 0.80, 0.10],
        is_howto=False,
    )

    results = rag_module.RAGSystem.search(rag, "alpha", knowledge_base_id=1, top_k=3)

    assert len(calls["rerank_pairs"]) == 3
    assert len(results) == 3
    assert [row["content"] for row in results] == ["dense-2", "dense-3", "dense-1"]


def test_search_generalized_mode_ignores_source_boost(monkeypatch):
    _set_legacy_heuristics(monkeypatch, enabled=False)
    monkeypatch.setattr(rag_module, "HAS_EMBEDDINGS", True, raising=False)
    monkeypatch.setattr(rag_module, "HAS_RERANKER", True, raising=False)
    rag, _calls = _build_test_system(
        dense_budget=2,
        bm25_budget=1,
        rerank_top_n=2,
        rerank_scores=[0.50, 0.60],
        is_howto=False,
        dense_candidates=[
            _candidate(
                "alpha-source",
                "doc://alpha/setup",
                distance=0.40,
                metadata={"doc_title": "Alpha Guide", "section_path": "alpha/guide"},
            ),
            _candidate("best-rerank", "doc://other", distance=0.30, metadata={"doc_title": "Other"}),
        ],
    )

    results = rag_module.RAGSystem.search(rag, "alpha guide", knowledge_base_id=1, top_k=1)

    assert results[0]["content"] == "best-rerank"
    assert results[0]["source_boost"] == 0.0


def test_search_legacy_mode_can_reenable_source_boost(monkeypatch):
    _set_legacy_heuristics(monkeypatch, enabled=True)
    monkeypatch.setattr(rag_module, "HAS_EMBEDDINGS", True, raising=False)
    monkeypatch.setattr(rag_module, "HAS_RERANKER", True, raising=False)
    rag, _calls = _build_test_system(
        dense_budget=2,
        bm25_budget=1,
        rerank_top_n=2,
        rerank_scores=[0.50, 0.60],
        is_howto=False,
        dense_candidates=[
            _candidate(
                "alpha-source",
                "doc://alpha/setup",
                distance=0.40,
                metadata={"doc_title": "Alpha Guide", "section_path": "alpha/guide"},
            ),
            _candidate("best-rerank", "doc://other", distance=0.30, metadata={"doc_title": "Other"}),
        ],
    )

    results = rag_module.RAGSystem.search(rag, "alpha guide", knowledge_base_id=1, top_k=1)

    assert results[0]["content"] == "alpha-source"
    assert results[0]["source_boost"] > 0.0


def test_search_generalized_mode_ignores_howto_fallback_sorting(monkeypatch):
    _set_legacy_heuristics(monkeypatch, enabled=False)
    monkeypatch.setattr(rag_module, "HAS_EMBEDDINGS", True, raising=False)
    monkeypatch.setattr(rag_module, "HAS_RERANKER", False, raising=False)
    rag, _calls = _build_test_system(
        dense_budget=1,
        bm25_budget=1,
        rerank_top_n=1,
        rerank_scores=[],
        is_howto=True,
        enable_rerank=False,
        dense_candidates=[
            _candidate(
                "dense-text",
                "doc://dense-text",
                distance=0.10,
                metadata={"chunk_kind": "text"},
            )
        ],
        bm25_chunks=[
            SimpleNamespace(
                content="bm25-code",
                chunk_metadata=json.dumps({"chunk_kind": "code_file"}, ensure_ascii=False),
                source_type="markdown",
                source_path="doc://bm25-code",
            )
        ],
    )

    results = rag_module.RAGSystem.search(rag, "how to build", knowledge_base_id=1, top_k=1)

    assert results[0]["content"] == "dense-text"
    assert results[0]["source_boost"] == 0.0


def test_search_legacy_mode_reenables_howto_fallback_sorting(monkeypatch):
    _set_legacy_heuristics(monkeypatch, enabled=True)
    monkeypatch.setattr(rag_module, "HAS_EMBEDDINGS", True, raising=False)
    monkeypatch.setattr(rag_module, "HAS_RERANKER", False, raising=False)
    rag, _calls = _build_test_system(
        dense_budget=1,
        bm25_budget=1,
        rerank_top_n=1,
        rerank_scores=[],
        is_howto=True,
        enable_rerank=False,
        dense_candidates=[
            _candidate(
                "dense-text",
                "doc://dense-text",
                distance=0.10,
                metadata={"chunk_kind": "text"},
            )
        ],
        bm25_chunks=[
            SimpleNamespace(
                content="bm25-code",
                chunk_metadata=json.dumps({"chunk_kind": "code_file"}, ensure_ascii=False),
                source_type="markdown",
                source_path="doc://bm25-code",
            )
        ],
    )

    results = rag_module.RAGSystem.search(rag, "how to build", knowledge_base_id=1, top_k=1)

    assert results[0]["content"] == "bm25-code"


def test_search_generalized_mode_uses_family_support_for_rerank_window(monkeypatch):
    _set_legacy_heuristics(monkeypatch, enabled=False)
    monkeypatch.setattr(rag_module, "HAS_EMBEDDINGS", True, raising=False)
    monkeypatch.setattr(rag_module, "HAS_RERANKER", True, raising=False)
    rag, calls = _build_test_system(
        dense_budget=2,
        bm25_budget=2,
        rerank_top_n=1,
        rerank_scores=[0.61],
        is_howto=False,
        dense_candidates=[
            _candidate(
                "singleton-dense",
                "doc://singleton",
                distance=0.10,
                metadata={"doc_title": "Singleton Guide", "section_path": "Singleton Guide > Start"},
                origin="dense",
            ),
            _candidate(
                "supported-family-primary",
                "doc://supported",
                distance=0.20,
                metadata={"doc_title": "Supported Guide", "section_path": "Supported Guide > Setup"},
                origin="dense",
            ),
        ],
        bm25_chunks=[
            _chunk(
                "supported-family-primary",
                "doc://supported",
                metadata={"doc_title": "Supported Guide", "section_path": "Supported Guide > Setup"},
            ),
            _chunk(
                "singleton-dense",
                "doc://singleton",
                metadata={"doc_title": "Singleton Guide", "section_path": "Singleton Guide > Start"},
            ),
        ],
        bm25_ranked=[0, 1],
        field_candidates=[
            _candidate(
                "supported-family-primary",
                "doc://supported",
                distance=0.05,
                metadata={"doc_title": "Supported Guide", "section_path": "Supported Guide > Setup"},
                origin="field",
            )
        ],
    )

    results = rag_module.RAGSystem.search(rag, "alpha setup", knowledge_base_id=1, top_k=1)

    assert len(calls["rerank_pairs"]) == 1
    assert calls["rerank_pairs"][0][1] == "supported-family-primary"
    assert results[0]["content"] == "supported-family-primary"
    assert results[0]["source_path"] == "doc://supported"


def test_search_generalized_mode_orders_by_family_support_without_reranker(monkeypatch):
    _set_legacy_heuristics(monkeypatch, enabled=False)
    monkeypatch.setattr(rag_module, "HAS_EMBEDDINGS", True, raising=False)
    monkeypatch.setattr(rag_module, "HAS_RERANKER", False, raising=False)
    rag, _calls = _build_test_system(
        dense_budget=2,
        bm25_budget=2,
        rerank_top_n=0,
        rerank_scores=[],
        is_howto=False,
        enable_rerank=False,
        dense_candidates=[
            _candidate(
                "singleton-dense",
                "doc://singleton",
                distance=0.10,
                metadata={"doc_title": "Singleton Guide", "section_path": "Singleton Guide > Start"},
                origin="dense",
            ),
            _candidate(
                "supported-family-primary",
                "doc://supported",
                distance=0.20,
                metadata={"doc_title": "Supported Guide", "section_path": "Supported Guide > Setup"},
                origin="dense",
            ),
        ],
        bm25_chunks=[
            _chunk(
                "supported-family-primary",
                "doc://supported",
                metadata={"doc_title": "Supported Guide", "section_path": "Supported Guide > Setup"},
            ),
            _chunk(
                "singleton-dense",
                "doc://singleton",
                metadata={"doc_title": "Singleton Guide", "section_path": "Singleton Guide > Start"},
            ),
        ],
        bm25_ranked=[0, 1],
        field_candidates=[
            _candidate(
                "supported-family-primary",
                "doc://supported",
                distance=0.05,
                metadata={"doc_title": "Supported Guide", "section_path": "Supported Guide > Setup"},
                origin="field",
            )
        ],
    )

    results = rag_module.RAGSystem.search(rag, "alpha setup", knowledge_base_id=1, top_k=2)

    assert [row["content"] for row in results] == ["supported-family-primary", "singleton-dense"]
    assert results[0]["source_path"] == "doc://supported"
