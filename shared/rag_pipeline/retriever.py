from typing import List

from shared.types import SearchFilters, SearchResult
from shared.rag_system import rag_system


def hybrid_search(query: str, filters: SearchFilters, top_k: int) -> List[SearchResult]:
    kb_id = filters.kb_id if filters else None
    results = rag_system.search(query, knowledge_base_id=kb_id, top_k=top_k)
    return [
        SearchResult(
            content=r.get("content", ""),
            score=float(r.get("rerank_score") or 0.0),
            source_path=r.get("source_path") or "",
            metadata=r.get("metadata") or {},
        )
        for r in results
    ]
