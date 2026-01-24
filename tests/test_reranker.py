import pytest

pytest.importorskip("numpy")

from shared.rag_pipeline.reranker import rerank
from shared.types import SearchResult


class DummyModel:
    def __init__(self, scores):
        self._scores = scores

    def predict(self, pairs):
        # Ignore pairs, return preset scores
        return self._scores


def test_rerank_orders_by_score():
    candidates = [
        SearchResult(content="a", score=0.0, source_path="a", metadata={}),
        SearchResult(content="b", score=0.0, source_path="b", metadata={}),
        SearchResult(content="c", score=0.0, source_path="c", metadata={}),
    ]
    model = DummyModel([0.2, 0.9, 0.5])
    ranked = rerank("q", candidates, top_k=2, model=model)

    assert [r.content for r in ranked] == ["b", "c"]
