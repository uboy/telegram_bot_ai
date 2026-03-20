"""
Semantic query cache for RAG retrieval (RAGPERF-002).
"""
import hashlib
import time
import threading
from collections import OrderedDict
from typing import Any, List, Dict, Optional, Tuple
from shared.logging_config import logger

class LRUCache:
    """In-process LRU cache with TTL and capacity limits."""
    
    def __init__(self, capacity: int, ttl_sec: int, max_total_entries: int):
        self.capacity = capacity
        self.ttl_sec = ttl_sec
        self.max_total_entries = max_total_entries
        self._cache: OrderedDict[Tuple[int, str], Dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def _normalize_query(self, query: str) -> str:
        """Collapse whitespace and lowercase."""
        return " ".join(query.lower().split())

    def _get_key(self, kb_id: int, query: str) -> Tuple[int, str]:
        """Generate SHA-256 hash key for (kb_id, query)."""
        norm = self._normalize_query(query)
        sha = hashlib.sha256(norm.encode("utf-8")).hexdigest()
        return (kb_id, sha)

    def get(self, kb_id: int, query: str) -> Optional[List[Dict[str, Any]]]:
        """Retrieve candidates from cache if exists and not expired."""
        key = self._get_key(kb_id, query)
        with self._lock:
            if key not in self._cache:
                return None
            
            entry = self._cache[key]
            # Check TTL
            if time.time() - entry["timestamp"] > self.ttl_sec:
                del self._cache[key]
                return None
            
            # Move to end (LRU)
            self._cache.move_to_end(key)
            return entry["candidates"]

    def set(self, kb_id: int, query: str, candidates: List[Dict[str, Any]]):
        """Store candidates in cache."""
        key = self._get_key(kb_id, query)
        with self._lock:
            # Add or update
            self._cache[key] = {
                "candidates": candidates,
                "timestamp": time.time()
            }
            self._cache.move_to_end(key)
            
            # Evict if over capacity (global)
            while len(self._cache) > self.max_total_entries:
                self._cache.popitem(last=False)
            
            # Note: per-KB capacity is harder to enforce strictly with global OrderedDict 
            # without complex bookkeeping, but global cap is sufficient for memory safety.

    def clear_kb(self, kb_id: int):
        """Remove all entries for a specific KB."""
        with self._lock:
            keys_to_del = [k for k in self._cache.keys() if k[0] == kb_id]
            for k in keys_to_del:
                del self._cache[k]
        if keys_to_del:
            logger.info("Cleared %d semantic cache entries for KB %s", len(keys_to_del), kb_id)

# Global instance initialized via config
rag_cache = None

def init_cache():
    global rag_cache
    try:
        from shared.config import RAG_CACHE_ENABLED, RAG_CACHE_CAPACITY, RAG_CACHE_TTL_SEC, RAG_CACHE_MAX_ENTRIES
        if RAG_CACHE_ENABLED:
            rag_cache = LRUCache(
                capacity=RAG_CACHE_CAPACITY,
                ttl_sec=RAG_CACHE_TTL_SEC,
                max_total_entries=RAG_CACHE_MAX_ENTRIES
            )
            logger.info("RAG semantic cache initialized (capacity=%d, ttl=%ds)", RAG_CACHE_CAPACITY, RAG_CACHE_TTL_SEC)
    except Exception as e:
        logger.warning("Failed to initialize RAG semantic cache: %s", e)

# Auto-initialize
init_cache()
