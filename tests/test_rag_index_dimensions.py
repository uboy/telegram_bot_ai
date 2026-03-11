import json
from types import SimpleNamespace

import numpy as np

from shared import rag_system as rag_module


def _fake_chunk(chunk_id: int, kb_id: int, dimension: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=chunk_id,
        knowledge_base_id=kb_id,
        embedding=json.dumps([0.1] * dimension),
        content=f"chunk-{chunk_id}",
        chunk_metadata="{}",
        source_type="web",
        source_path=f"doc://{kb_id}/{chunk_id}",
    )


def test_group_chunk_embeddings_by_kb_keeps_separate_dimensions():
    grouped = rag_module._group_chunk_embeddings_by_kb(
        [
            _fake_chunk(1, 1, 384),
            _fake_chunk(2, 1, 384),
            _fake_chunk(3, 2, 768),
            _fake_chunk(4, 2, 768),
        ]
    )

    assert grouped["expected_dim_by_kb"] == {1: 384, 2: 768}
    assert grouped["dim_mismatches_by_kb"] == {}
    assert len(grouped["chunks_by_kb"][1]) == 2
    assert len(grouped["chunks_by_kb"][2]) == 2
    assert grouped["chunks_with_embedding"] == 4


def test_group_chunk_embeddings_by_kb_skips_only_intra_kb_mismatches():
    grouped = rag_module._group_chunk_embeddings_by_kb(
        [
            _fake_chunk(1, 1, 384),
            _fake_chunk(2, 1, 768),
            _fake_chunk(3, 2, 768),
        ]
    )

    assert grouped["expected_dim_by_kb"] == {1: 384, 2: 768}
    assert grouped["dim_mismatches_by_kb"] == {1: 1}
    assert len(grouped["chunks_by_kb"][1]) == 1
    assert len(grouped["chunks_by_kb"][2]) == 1
    assert grouped["chunks_with_embedding"] == 2


def test_search_falls_back_when_query_dimension_mismatches_kb_index(monkeypatch):
    rag = object.__new__(rag_module.RAGSystem)
    rag.encoder = object()
    rag.index = None
    rag.chunks = []
    rag.index_by_kb = {5: object()}
    rag.index_dimension_by_kb = {5: 768}
    rag.chunks_by_kb = {5: [SimpleNamespace()]}
    rag.bm25_index_by_kb = {}
    rag.bm25_index_all = None
    rag.bm25_chunks_by_kb = {5: []}
    rag.bm25_chunks_all = []
    rag.enable_rerank = False
    rag.reranker = None
    rag.max_candidates = 10
    rag.dense_candidate_budget = 5
    rag.bm25_candidate_budget = 5
    rag.rerank_top_n = 5
    rag.dimension = 384
    rag._load_index = lambda _kb_id: None
    rag._qdrant_enabled = lambda: False
    rag._get_embedding = lambda _query: np.array([0.1] * 384, dtype="float32")
    rag._simple_search = lambda query, knowledge_base_id, top_k: [  # noqa: ARG005
        {"content": "keyword-only", "source_path": "doc://fallback", "distance": 0.0}
    ]

    result = rag_module.RAGSystem.search(rag, "how to build and sync", knowledge_base_id=5, top_k=3)

    assert result == [{"content": "keyword-only", "source_path": "doc://fallback", "distance": 0.0}]
