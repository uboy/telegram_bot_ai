from typing import List, Optional, Any

from shared.types import SearchResult


def rerank(
    query: str,
    candidates: List[SearchResult],
    top_k: int,
    model: Optional[Any] = None,
) -> List[SearchResult]:
    """
    Rerank candidates using an optional cross-encoder model.
    """
    if not candidates:
        return []
    if not model:
        return candidates[:top_k]

    pairs = [[query, c.content] for c in candidates]
    scores = model.predict(pairs)
    scored = []
    for cand, score in zip(candidates, scores):
        scored.append(SearchResult(
            content=cand.content,
            score=float(score),
            source_path=cand.source_path,
            metadata=cand.metadata,
        ))
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:top_k]
