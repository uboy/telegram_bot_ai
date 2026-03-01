import pytest

pytest.importorskip("fastapi")

from backend.api.routes.rag import rag_summary
from backend.schemas.rag import RAGSummaryQuery


class DummyDB:
    pass


def _mock_search(*_args, **_kwargs):
    return [
        {
            "content": "Step 1 do this. Step 2 do that.",
            "metadata": {"source_updated_at": "2025-01-01T10:00:00"},
            "source_path": "doc://guide",
            "source_type": "markdown",
        }
    ]


def test_rag_summary_mode_faq(monkeypatch):
    from backend.api.routes import rag as rag_module

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(_mock_search)})())
    monkeypatch.setattr(rag_module, "ai_manager", type("Y", (), {"query": staticmethod(lambda prompt: prompt)})())

    payload = RAGSummaryQuery(query="Generate faq", knowledge_base_id=1, mode="faq")
    result = rag_summary(payload, db=DummyDB())
    assert "Составь FAQ" in result.answer


def test_rag_summary_mode_instructions(monkeypatch):
    from backend.api.routes import rag as rag_module

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(_mock_search)})())
    monkeypatch.setattr(rag_module, "ai_manager", type("Y", (), {"query": staticmethod(lambda prompt: prompt)})())

    payload = RAGSummaryQuery(query="Generate instructions", knowledge_base_id=1, mode="instructions")
    result = rag_summary(payload, db=DummyDB())
    assert "Составь пошаговую инструкцию" in result.answer
