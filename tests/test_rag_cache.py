"""
Tests for semantic query cache (RAGPERF-002).
"""
import pytest
import time
from unittest.mock import patch, MagicMock
from shared.cache import LRUCache

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
