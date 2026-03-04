import pytest

pytest.importorskip("fastapi")

from backend.api.routes.rag import rag_query
from backend.schemas.rag import RAGQuery


class DummyKB:
    settings = {
        "rag": {
            "single_page_mode": True,
            "single_page_top_k": 1,
            "full_page_context_multiplier": 5,
        }
    }


class DummyQuery:
    def filter_by(self, **_kwargs):
        return self

    def first(self):
        return DummyKB()


class DummyDB:
    def query(self, _model):
        return DummyQuery()


def test_rag_query_definition_prefers_definition_chunk(monkeypatch):
    from backend.api.routes import rag as rag_module

    def fake_search(query, knowledge_base_id=None, top_k=8):
        return [
            {
                "content": (
                    "В документе создается механизм гарантированного обезличивания и "
                    "разметки данных при соблюдении прав обладателей информации."
                ),
                "metadata": {"section_title": "Приоритеты", "section_path": "пункт 42"},
                "source_path": "doc://policy",
                "source_type": "pdf",
                "rerank_score": 0.92,
                "distance": 0.08,
            },
            {
                "content": (
                    "Разметка данных - этап обработки структурированных и "
                    "неструктурированных данных, при котором данным присваиваются "
                    "идентификаторы."
                ),
                "metadata": {"section_title": "Термины и определения", "section_path": "пункт 2"},
                "source_path": "doc://glossary",
                "source_type": "pdf",
                "rerank_score": 0.70,
                "distance": 0.30,
            },
        ]

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(fake_search)})())
    monkeypatch.setattr(rag_module, "ai_manager", type("Y", (), {"query": staticmethod(lambda prompt: prompt)})())

    payload = RAGQuery(
        query="Как в документе определяется разметка данных?",
        knowledge_base_id=1,
    )
    result = rag_query(payload, db=DummyDB())

    assert "Разметка данных - этап обработки" in result.answer
    assert "гарантированного обезличивания" not in result.answer
