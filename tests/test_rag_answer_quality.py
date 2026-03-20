"""
Tests for RAG Answer Quality Evaluation (RAGEVAL-001).
"""
import pytest
import json
import uuid
from unittest.mock import patch, MagicMock
from shared.database import get_session, RAGAnswerFeedback, RetrievalQueryLog, KnowledgeBase, RAGEvalRun, RAGEvalResult
from shared.rag_judge import score_answer, is_intentional_refusal
from backend.services.rag_eval_service import rag_eval_service
from fastapi.testclient import TestClient
from backend.app import app

client = TestClient(app)

class TestUserFeedback:
    """Tests for user feedback API and persistence."""

    def test_feedback_persisted_correctly(self):
        """POST /feedback should save to DB and link to KB if possible."""
        request_id = f"test_req_{uuid.uuid4().hex[:8]}"
        kb_name = f"test_kb_{uuid.uuid4().hex[:8]}"
        with get_session() as session:
            # Setup: Create a KB and a log entry
            kb = KnowledgeBase(name=kb_name, embedding_model="test")
            session.add(kb)
            session.commit()
            kb_id = kb.id
            
            # RetrievalQueryLog does not have 'answer' field (it's in AI metrics)
            log = RetrievalQueryLog(
                request_id=request_id,
                query="test query",
                knowledge_base_id=kb_id
            )
            session.add(log)
            session.commit()

        try:
            # Act: Send feedback
            response = client.post(
                "/api/v1/rag/feedback",
                json={
                    "request_id": request_id,
                    "vote": "helpful",
                    "comment": "Very useful!",
                    "user_id": 999
                },
                headers={"X-API-Key": "test_api_key"}
            )
            
            assert response.status_code == 200
            assert response.json() == {"ok": True}
            
            # Verify: Check DB
            with get_session() as session:
                fb = session.query(RAGAnswerFeedback).filter_by(request_id=request_id).first()
                assert fb is not None
                assert fb.vote == "helpful"
                assert fb.comment == "Very useful!"
                assert fb.kb_id == kb_id
                assert fb.user_id == 999
        finally:
            # Cleanup
            with get_session() as session:
                session.query(RAGAnswerFeedback).filter_by(request_id=request_id).delete()
                session.query(RetrievalQueryLog).filter_by(request_id=request_id).delete()
                session.query(KnowledgeBase).filter_by(id=kb_id).delete()
                session.commit()

    def test_feedback_swallows_db_errors(self):
        """POST /feedback should return ok:True even if DB fails."""
        with patch("backend.api.routes.rag.get_db_dep") as mock_get_db:
            mock_db = MagicMock()
            mock_db.query.side_effect = Exception("DB Down")
            mock_get_db.return_value = mock_db
            
            response = client.post(
                "/api/v1/rag/feedback",
                json={"request_id": "invalid", "vote": "not_helpful"},
                headers={"X-API-Key": "test_api_key"}
            )
            assert response.status_code == 200
            assert response.json() == {"ok": True}


class TestJudgeLogic:
    """Tests for LLM-as-judge scoring and refusal detection."""

    def test_intentional_refusal_detection(self):
        """Verify various refusal patterns are caught."""
        # Using explicit patterns from rag_judge.py
        assert is_intentional_refusal("Информации недостаточно для ответа.")
        assert is_intentional_refusal("I cannot answer from the provided context.")
        assert is_intentional_refusal("Я не знаю ответа.")
        assert not is_intentional_refusal("Кнопка находится в левом углу.")

    @patch("shared.ai_providers.ai_manager.query")
    def test_judge_scoring_parsing(self, mock_query):
        """Verify judge response JSON parsing and score validation."""
        mock_query.return_value = '{"faithfulness": 0.9, "relevance": 0.8, "completeness": 1.0, "reasoning": "Good answer"}'
        
        result = score_answer("query", "answer", ["context"])
        
        assert result["faithfulness"] == 0.9
        assert result["relevance"] == 0.8
        assert result["completeness"] == 1.0
        assert result["reasoning"] == "Good answer"
        assert not result["judge_skipped"]

    @patch("shared.ai_providers.ai_manager.query")
    def test_judge_handles_malformed_json(self, mock_query):
        """Judge should handle text-wrapped JSON or invalid formats."""
        # Scenario 1: Text-wrapped JSON
        mock_query.return_value = "Here is the result: " + '{"faithfulness": 0.5, "relevance": 0.5, "completeness": 0.5, "reasoning": "Ok"}'
        result = score_answer("q", "a", ["c"])
        assert result["faithfulness"] == 0.5
        
        # Scenario 2: Total garbage
        mock_query.return_value = "Error 500"
        result = score_answer("q", "a", ["c"])
        assert result["judge_skipped"] is True
        assert result["faithfulness"] == 0.0


class TestEvalServiceJudgeIntegration:
    """Tests for run_with_judge flag in RAGEvalService."""

    @patch("backend.services.rag_eval_service._load_yaml_suite")
    @patch("backend.services.rag_eval_service._run_answer_case")
    def test_eval_run_calls_judge_when_flag_set(self, mock_run_case, mock_load_suite):
        """If run_with_judge=True, judge should be invoked for cases."""
        mock_load_suite.return_value = [{"id": "case1", "query": "q1", "tags": []}]
        mock_run_case.return_value = {
            "metrics": {"faithfulness": 1.0},
            "judge_skipped": False
        }
        
        # Mock DB
        with patch("shared.database.get_session"):
            rag_eval_service._execute_run(
                run_id="test_run_judge",
                suite_name="test_suite",
                baseline_run_id=None,
                requested_slices=["overall"],
                slices_explicit=True,
                run_with_judge=True
            )
        
        # Check if run_with_judge was passed down
        args, kwargs = mock_run_case.call_args
        assert kwargs["run_with_judge"] is True
