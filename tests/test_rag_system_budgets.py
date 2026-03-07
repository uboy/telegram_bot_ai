import json
from types import SimpleNamespace

import numpy as np

from shared import rag_system as rag_module


class DummyReranker:
    def __init__(self, scores, calls):
        self._scores = list(scores)
        self._calls = calls

    def predict(self, pairs):
        self._calls["rerank_pairs"] = list(pairs)
        return self._scores[: len(pairs)]


def _chunk(content: str, source_path: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        chunk_metadata=json.dumps({"doc_title": source_path}, ensure_ascii=False),
        source_type="markdown",
        source_path=source_path,
    )


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


def _build_test_system(*, dense_budget: int, bm25_budget: int, rerank_top_n: int, rerank_scores, is_howto: bool):
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
        1: [
            _chunk("bm25-1", "doc://bm25-1"),
            _chunk("bm25-2", "doc://bm25-2"),
            _chunk("bm25-3", "doc://bm25-3"),
            _chunk("bm25-4", "doc://bm25-4"),
            _chunk("bm25-5", "doc://bm25-5"),
            _chunk("bm25-6", "doc://bm25-6"),
        ]
    }
    rag.bm25_chunks_all = []
    rag.enable_rerank = True
    rag.reranker = DummyReranker(rerank_scores, calls)
    rag.max_candidates = max(dense_budget, bm25_budget)
    rag.dense_candidate_budget = dense_budget
    rag.bm25_candidate_budget = bm25_budget
    rag.rerank_top_n = rerank_top_n
    rag._load_index = lambda _knowledge_base_id: None
    rag._get_embedding = lambda _query: np.array([0.1, 0.2], dtype="float32")
    rag._is_howto_query = lambda _query: is_howto
    rag._qdrant_enabled = lambda: True
    rag._rrf_fuse = _flatten_unique

    def dense_search(*, query_embedding, knowledge_base_id, top_k):  # noqa: ARG001
        calls["dense_top_k"] = top_k
        return [
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
        ][:top_k]

    def bm25_search(_query, _bm25_index, limit):
        calls["bm25_limit"] = limit
        return list(range(limit))

    rag._qdrant_dense_search = dense_search
    rag._bm25_search = bm25_search
    return rag, calls


def test_search_uses_explicit_channel_budgets_and_rerank_window(monkeypatch):
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
