from datetime import datetime
import pytest

pytest.importorskip("fastapi")

from backend.api.routes.rag import rag_summary
from backend.schemas.rag import RAGSummaryQuery


class DummyDB:
    pass


def test_rag_summary_filters_dates(monkeypatch):
    from backend.api.routes import rag as rag_module

    def fake_search(query, knowledge_base_id=None, top_k=8):
        return [
            {"content": "a", "metadata": {"source_updated_at": "2024-01-01T10:00:00"}, "source_path": "p1", "source_type": "chat"},
            {"content": "b", "metadata": {"source_updated_at": "2024-02-01T10:00:00"}, "source_path": "p2", "source_type": "chat"},
        ]

    def fake_ai(prompt):
        return prompt

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(fake_search)})())
    monkeypatch.setattr(rag_module, "ai_manager", type("Y", (), {"query": staticmethod(fake_ai)})())

    payload = RAGSummaryQuery(query="x", knowledge_base_id=1, mode="summary", date_from="2024-01-15")
    result = rag_summary(payload, db=DummyDB())
    assert "b" in result.answer
