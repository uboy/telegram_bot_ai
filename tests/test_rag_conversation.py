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
