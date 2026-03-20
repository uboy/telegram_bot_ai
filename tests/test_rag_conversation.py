"""
Tests for conversation-aware query reformulation (RAGCONV-001).
"""
import pytest
from unittest.mock import patch, MagicMock
from shared.rag_pipeline.query_rewriter import is_follow_up, reformulate_query

class TestQueryRewriter:
    def test_is_follow_up_heuristics(self):
        history = [{"role": "user", "text": "Who is the CEO?"}, {"role": "assistant", "text": "The CEO is John Doe."}]
        
        # Pronouns
        assert is_follow_up("А что насчет него?", history) is True
        assert is_follow_up("How about that?", history) is True
        assert is_follow_up("this one", history) is True
        
        # Length (short)
        assert is_follow_up("его контакты", history) is True
        
        # Not a follow-up (long, no pronouns)
        assert is_follow_up("What is the current revenue of the company in 2025?", history) is False
        
        # Empty history
        assert is_follow_up("this", []) is False

    @patch("shared.ai_providers.ai_manager.query")
    def test_reformulate_query_applied(self, mock_query):
        mock_query.return_value = "What is John Doe's salary?"
        history = [
            {"role": "user", "text": "Who is John Doe?"},
            {"role": "assistant", "text": "He is the CEO."}
        ]
        query = "What is his salary?"
        
        rewritten, applied, turns = reformulate_query(query, history)
        
        assert applied is True
        assert rewritten == "What is John Doe's salary?"
        assert turns == 2
        mock_query.assert_called_once()

    def test_reformulate_query_skipped_no_history(self):
        query = "What is his salary?"
        rewritten, applied, turns = reformulate_query(query, [])
        assert applied is False
        assert rewritten == query
        assert turns == 0

    def test_is_follow_up_short_query_without_pronouns(self):
        """A very short query (< 6 content tokens) triggers follow-up even without explicit pronouns."""
        history = [{"role": "user", "text": "Tell me about CI/CD."},
                   {"role": "assistant", "text": "CI/CD is a pipeline."}]
        # 2 content tokens — should trigger
        assert is_follow_up("подробнее про пайплайн", history) is True
        # Long query without pronouns — should NOT trigger
        assert is_follow_up("Как настроить pipeline для сборки Android проекта с нуля?", history) is False

    @patch("shared.ai_providers.ai_manager.query")
    def test_reformulate_query_fallback_on_llm_error(self, mock_query):
        """When LLM raises, reformulate_query returns the original query with applied=False."""
        mock_query.side_effect = RuntimeError("connection error")
        history = [{"role": "user", "text": "What is Docker?"},
                   {"role": "assistant", "text": "Docker is a container platform."}]
        query = "How does it work?"

        rewritten, applied, turns = reformulate_query(query, history)

        assert applied is False
        assert rewritten == query

    def test_rag_query_uses_reformulated_query_when_context_provided(self, monkeypatch):
        """When conversation_context is passed in RAGQuery, the rewriter is called."""
        pytest.importorskip("fastapi")
        from backend.api.routes import rag as rag_route_module
        from backend.schemas.rag import RAGQuery, ConversationTurn

        rewriter_calls = []

        def fake_reformulate(query, history, model=None, provider=None):
            rewriter_calls.append({"query": query, "history": history})
            return "reformulated: " + query, True, len(history)

        monkeypatch.setattr(rag_route_module, "reformulate_query", fake_reformulate)

        # Minimal stubs so rag_query doesn't crash before rewriter check
        monkeypatch.setattr(rag_route_module, "rag_system", MagicMock(search=lambda **kw: []))
        monkeypatch.setattr(rag_route_module, "ai_manager", MagicMock(query=lambda *a, **kw: "answer"))

        class FakeDB:
            def query(self, *a): return self
            def filter_by(self, **kw): return self
            def filter(self, *a): return self
            def first(self): return None
            def add(self, *a): pass
            def commit(self): pass
            def rollback(self): pass

        payload = RAGQuery(
            query="как это работает?",
            knowledge_base_id=None,
            conversation_context=[
                ConversationTurn(role="user", text="Что такое Docker?"),
                ConversationTurn(role="assistant", text="Docker — платформа контейнеризации."),
            ],
        )

        try:
            rag_route_module.rag_query(payload, db=FakeDB())
        except Exception:
            pass  # We only care that the rewriter was called

        assert len(rewriter_calls) == 1
        assert rewriter_calls[0]["query"] == "как это работает?"
        assert len(rewriter_calls[0]["history"]) == 2
