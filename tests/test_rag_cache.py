"""
Tests for semantic query cache (RAGPERF-002).
"""
import numpy as np
import pytest
import time
from unittest.mock import patch, MagicMock
from shared.cache import LRUCache
from shared import rag_system as rag_module

class TestSemanticCache:
    def test_cache_set_get(self):
        cache = LRUCache(capacity=10, ttl_sec=60, max_total_entries=100)
        kb_id = 1
        query = "test query"
        candidates = [{"id": 1, "content": "result"}]
        
        cache.set(kb_id, query, candidates)
        res = cache.get(kb_id, query)
        
        assert res == candidates
        # Normalized query check
        assert cache.get(kb_id, "  TEST  query  ") == candidates

    def test_cache_ttl(self):
        cache = LRUCache(capacity=10, ttl_sec=1, max_total_entries=100)
        kb_id = 1
        query = "quick query"
        
        cache.set(kb_id, query, [{"id": 1}])
        assert cache.get(kb_id, query) is not None
        
        time.sleep(1.1)
        assert cache.get(kb_id, query) is None

    def test_cache_lru_eviction(self):
        # Max entries = 2
        cache = LRUCache(capacity=10, ttl_sec=60, max_total_entries=2)
        
        cache.set(1, "q1", [{"id": 1}])
        cache.set(1, "q2", [{"id": 2}])
        cache.set(1, "q3", [{"id": 3}]) # Should evict q1
        
        assert cache.get(1, "q1") is None
        assert cache.get(1, "q2") is not None
        assert cache.get(1, "q3") is not None

    def test_clear_kb(self):
        cache = LRUCache(capacity=10, ttl_sec=60, max_total_entries=100)
        cache.set(1, "q1", [{"id": 1}])
        cache.set(2, "q2", [{"id": 2}])

        cache.clear_kb(1)
        assert cache.get(1, "q1") is None
        assert cache.get(2, "q2") is not None


def _build_cached_system(cache_instance):
    """Build a minimal RAGSystem with cache_enabled=True and injected cache."""
    rag = object.__new__(rag_module.RAGSystem)
    rag.encoder = object()
    rag.index = None
    rag.chunks = []
    rag.index_by_kb = {1: MagicMock()}
    rag.chunks_by_kb = {1: [MagicMock(id=1, content="c", chunk_metadata="{}", source_type="md", source_path="p")]}
    rag.bm25_index_by_kb = {1: {}}  # empty → BM25 returns [] without crashing
    rag.bm25_index_all = None
    rag.bm25_chunks_by_kb = {1: []}
    rag.bm25_chunks_all = []
    rag.enable_rerank = False
    rag.reranker = None
    rag.max_candidates = 10
    rag.dense_candidate_budget = 10
    rag.bm25_candidate_budget = 10
    rag.rerank_top_n = 10
    rag.cache_enabled = True
    rag.hyde_enabled = False
    rag.retrieval_backend = "legacy"
    rag._qdrant_bootstrap_done = False
    rag._load_index = lambda _kb_id: None
    rag._get_embedding = lambda text, is_query=False: np.array([0.1, 0.2], dtype="float32")
    rag._qdrant_enabled = lambda: False
    rag.index_dimension_by_kb = {1: 2}
    return rag


class TestCacheIntegration:
    def test_cache_hit_skips_dense_search(self):
        """A cache hit must return results without calling FAISS search."""
        cache = LRUCache(capacity=10, ttl_sec=60, max_total_entries=100)
        cached_result = [{"id": 99, "content": "cached", "cache_hit": True}]
        cache.set(1, "how to build", cached_result)

        rag = _build_cached_system(cache)

        search_called = []
        index_mock = MagicMock()
        index_mock.ntotal = 1
        index_mock.search = lambda emb, k: (search_called.append(True), (np.array([[0.9]]), np.array([[0]])))[1]
        rag.index_by_kb = {1: index_mock}

        # Patch rag_module.rag_cache — that's what search() references at runtime
        with patch.object(rag_module, "rag_cache", cache):
            results = rag.search("how to build", knowledge_base_id=1, top_k=5)

        assert search_called == [], "FAISS search must NOT be called on cache hit"
        assert any(r.get("id") == 99 for r in results)

    def test_cache_miss_populates_cache(self):
        """After a cache miss, results are stored in cache for subsequent calls."""
        cache = LRUCache(capacity=10, ttl_sec=60, max_total_entries=100)

        rag = _build_cached_system(cache)
        chunk = MagicMock(id=42, content="chunk content", chunk_metadata="{}", source_type="md", source_path="p")
        index_mock = MagicMock()
        index_mock.ntotal = 1
        index_mock.search = lambda emb, k: (np.array([[0.9]]), np.array([[0]]))
        rag.index_by_kb = {1: index_mock}
        rag.chunks_by_kb = {1: [chunk]}

        with patch.object(rag_module, "rag_cache", cache):
            rag.search("how to deploy", knowledge_base_id=1, top_k=1)
            # Second call should hit cache — FAISS must not be called again
            search_calls = []
            index_mock.search = lambda emb, k: (search_calls.append(True), (np.array([[0.9]]), np.array([[0]])))[1]
            rag.search("how to deploy", knowledge_base_id=1, top_k=1)

        assert search_calls == [], "Second identical query must be served from cache"

    def test_load_index_clears_cache_for_kb(self):
        """When _load_index rebuilds a KB, it must invalidate that KB's cache entries."""
        import threading
        from contextlib import contextmanager
        from shared import cache as cache_module
        from unittest.mock import MagicMock

        cache = LRUCache(capacity=10, ttl_sec=60, max_total_entries=100)
        cache.set(1, "stale query", [{"id": 1, "content": "stale"}])

        MODEL = "intfloat/multilingual-e5-base"

        # Fake KB and chunks returned from DB
        fake_kb = MagicMock()
        fake_kb.embedding_model = MODEL

        fake_session = MagicMock()
        fake_session.query.return_value.filter_by.return_value.first.return_value = fake_kb
        fake_session.query.return_value.filter_by.return_value.filter.return_value.all.return_value = []
        fake_session.query.return_value.filter_by.return_value.all.return_value = []
        fake_session.query.return_value.filter.return_value.all.return_value = []

        @contextmanager
        def fake_get_session():
            yield fake_session

        rag = object.__new__(rag_module.RAGSystem)
        rag.cache_enabled = True
        rag.persist_enabled = False
        rag.model_name = MODEL
        rag.retrieval_backend = "legacy"
        rag._qdrant_bootstrap_done = False
        rag._pending_rebuild_lock = threading.Lock()
        rag._pending_rebuild_kbs = set()
        rag.encoder = None  # HAS_EMBEDDINGS path will return early after cache clear

        with patch.object(rag_module, "rag_cache", cache), \
             patch("shared.rag_system.get_session", fake_get_session):
            try:
                rag._load_index(1)
            except Exception:
                pass  # index build may fail without encoder — that's expected

        assert cache.get(1, "stale query") is None, (
            "_load_index must clear cache for KB 1 before rebuilding"
        )
