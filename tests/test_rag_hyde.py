"""
Tests for HyDE (Hypothetical Document Embeddings) — RAGPERF-003.
"""
import numpy as np
import pytest
from unittest.mock import patch, MagicMock

from shared import rag_system as rag_module


def _build_hyde_system(*, hyde_enabled: bool, embedding_dim: int = 4):
    """Build a minimal RAGSystem with hyde_enabled set, bypassing __init__."""
    rag = object.__new__(rag_module.RAGSystem)
    rag.encoder = object()
    rag.index = None
    rag.chunks = []
    rag.index_by_kb = {1: MagicMock()}
    rag.chunks_by_kb = {1: []}
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
    rag.cache_enabled = False
    rag.hyde_enabled = hyde_enabled
    rag.hyde_max_tokens = 80
    rag.retrieval_backend = "legacy"
    rag._qdrant_bootstrap_done = False
    rag._load_index = lambda _kb_id: None
    rag.index_dimension_by_kb = {1: embedding_dim}
    return rag


class TestHyDEDisabled:
    def test_hyde_disabled_does_not_call_llm(self, monkeypatch):
        """When hyde_enabled=False, _hyde_generate_hypothetical_doc must not be called."""
        rag = _build_hyde_system(hyde_enabled=False)
        embedding = np.array([0.1, 0.2, 0.3, 0.4], dtype="float32")
        rag._get_embedding = lambda text, is_query=False: embedding
        rag._qdrant_enabled = lambda: False

        hyde_calls = []
        original_hyde = rag_module.RAGSystem._hyde_generate_hypothetical_doc
        rag._hyde_generate_hypothetical_doc = lambda q: hyde_calls.append(q) or None

        rag.search("how to build", knowledge_base_id=1, top_k=1)

        assert hyde_calls == [], "HyDE must not be called when hyde_enabled=False"

    def test_hyde_disabled_uses_original_embedding(self, monkeypatch):
        """With HyDE off, query embedding is used directly for dense search."""
        rag = _build_hyde_system(hyde_enabled=False)
        original_emb = np.array([1.0, 0.0, 0.0, 0.0], dtype="float32")
        captured = {}

        def fake_get_embedding(text, is_query=False):
            captured["last_embedding"] = original_emb.copy()
            captured["last_is_query"] = is_query
            return original_emb

        rag._get_embedding = fake_get_embedding
        rag._qdrant_enabled = lambda: False

        rag.search("how to build", knowledge_base_id=1, top_k=1)

        assert captured.get("last_is_query") is True


class TestHyDEEnabled:
    def test_hyde_replaces_query_embedding(self):
        """When HyDE is enabled and LLM succeeds, the hypothetical-doc embedding replaces the query embedding."""
        rag = _build_hyde_system(hyde_enabled=True)
        query_emb = np.array([1.0, 0.0, 0.0, 0.0], dtype="float32")
        hyde_emb = np.array([0.0, 1.0, 0.0, 0.0], dtype="float32")
        used_embeddings = []

        def fake_get_embedding(text, is_query=False):
            return query_emb

        def fake_faiss_search(index, emb, k):
            used_embeddings.append(emb.copy())
            return np.array([[0.9]]), np.array([[0]])

        rag._get_embedding = fake_get_embedding
        rag._hyde_generate_hypothetical_doc = lambda q: hyde_emb
        rag._qdrant_enabled = lambda: False

        with patch.object(rag_module, "faiss") as mock_faiss:
            mock_faiss.IndexFlatL2 = MagicMock()
            index_mock = MagicMock()
            index_mock.ntotal = 1
            index_mock.search = lambda emb, k: fake_faiss_search(index_mock, emb, k)
            rag.index_by_kb = {1: index_mock}
            rag.chunks_by_kb = {1: [MagicMock(id=1, content="chunk", chunk_metadata="{}", source_type="md", source_path="p")]}

            rag.search("how to build", knowledge_base_id=1, top_k=1)

        # The embedding passed to FAISS must be the HyDE embedding, not the query embedding
        assert len(used_embeddings) > 0
        assert np.allclose(used_embeddings[0], hyde_emb), (
            "Dense search must use HyDE embedding, not raw query embedding"
        )

    def test_hyde_fallback_on_llm_error(self):
        """When HyDE LLM call raises, falls back to original query embedding without crashing."""
        rag = _build_hyde_system(hyde_enabled=True)
        query_emb = np.array([1.0, 0.0, 0.0, 0.0], dtype="float32")
        used_embeddings = []

        def fake_get_embedding(text, is_query=False):
            return query_emb

        def failing_hyde(query):
            raise RuntimeError("LLM timeout")

        def fake_faiss_search(emb, k):
            used_embeddings.append(emb.copy())
            return np.array([[0.9]]), np.array([[0]])

        rag._get_embedding = fake_get_embedding
        rag._hyde_generate_hypothetical_doc = failing_hyde
        rag._qdrant_enabled = lambda: False

        index_mock = MagicMock()
        index_mock.ntotal = 1
        index_mock.search = fake_faiss_search
        rag.index_by_kb = {1: index_mock}
        rag.chunks_by_kb = {1: [MagicMock(id=1, content="chunk", chunk_metadata="{}", source_type="md", source_path="p")]}

        # Must not raise, must use query embedding
        rag.search("how to build", knowledge_base_id=1, top_k=1)
        assert len(used_embeddings) > 0
        assert np.allclose(used_embeddings[0], query_emb), (
            "On HyDE failure, must fall back to original query embedding"
        )

    def test_hyde_fallback_on_empty_response(self):
        """When HyDE returns None (empty LLM response), original embedding is used."""
        rag = _build_hyde_system(hyde_enabled=True)
        query_emb = np.array([1.0, 0.0, 0.0, 0.0], dtype="float32")
        used_embeddings = []

        rag._get_embedding = lambda text, is_query=False: query_emb
        rag._hyde_generate_hypothetical_doc = lambda q: None  # empty / too-short response
        rag._qdrant_enabled = lambda: False

        index_mock = MagicMock()
        index_mock.ntotal = 1
        index_mock.search = lambda emb, k: (used_embeddings.append(emb.copy()), (np.array([[0.9]]), np.array([[0]])))[1]
        rag.index_by_kb = {1: index_mock}
        rag.chunks_by_kb = {1: [MagicMock(id=1, content="chunk", chunk_metadata="{}", source_type="md", source_path="p")]}

        rag.search("how to build", knowledge_base_id=1, top_k=1)
        assert len(used_embeddings) > 0
        assert np.allclose(used_embeddings[0], query_emb)


class TestHyDEGenerate:
    def test_generate_calls_ai_manager_with_max_tokens(self):
        """_hyde_generate_hypothetical_doc passes max_tokens to ai_manager.query."""
        rag = _build_hyde_system(hyde_enabled=True)
        rag.hyde_max_tokens = 80

        called_kwargs = {}
        rag._get_embedding = lambda text, is_query=False: np.array([0.1, 0.2], dtype="float32")

        with patch("shared.ai_providers.ai_manager.query") as mock_query:
            mock_query.return_value = "Here is a hypothetical answer about building the project."
            result = rag._hyde_generate_hypothetical_doc("how to build the project")

        assert result is not None
        _, kwargs = mock_query.call_args
        assert kwargs.get("max_tokens") == 80

    def test_generate_uses_passage_embedding(self):
        """_hyde_generate_hypothetical_doc must call _get_embedding with is_query=False."""
        rag = _build_hyde_system(hyde_enabled=True)
        captured = {}

        def fake_embed(text, is_query=False):
            captured["is_query"] = is_query
            return np.array([0.1, 0.2], dtype="float32")

        rag._get_embedding = fake_embed

        with patch("shared.ai_providers.ai_manager.query") as mock_query:
            mock_query.return_value = "A detailed passage about the topic."
            rag._hyde_generate_hypothetical_doc("what is the topic")

        assert captured.get("is_query") is False, (
            "HyDE hypothetical doc must be embedded as a passage (is_query=False), not as a query"
        )
