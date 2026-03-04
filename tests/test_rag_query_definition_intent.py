import pytest
import json

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
    def __init__(self, rows=None):
        self._rows = rows or []
        self._limit = None

    def filter_by(self, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self._limit = value
        return self

    def all(self):
        rows = list(self._rows)
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    def first(self):
        if self._rows:
            return self._rows[0]
        return DummyKB()


class DummyChunkRow:
    def __init__(self, content: str, source_path: str = "doc://policy", source_type: str = "pdf", metadata: dict | None = None):
        self.id = 999
        self.content = content
        self.chunk_metadata = json.dumps(metadata or {}, ensure_ascii=False)
        self.source_type = source_type
        self.source_path = source_path
        self.is_deleted = False


class DummyDB:
    def __init__(self, chunk_rows=None):
        self._chunk_rows = chunk_rows or []

    def query(self, _model):
        model_name = getattr(_model, "__name__", "")
        if model_name == "KnowledgeBase":
            return DummyQuery(rows=[DummyKB()])
        if model_name == "KnowledgeChunk":
            return DummyQuery(rows=self._chunk_rows)
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


def test_rag_query_point_uses_keyword_fallback_chunk(monkeypatch):
    from backend.api.routes import rag as rag_module

    def fake_search(query, knowledge_base_id=None, top_k=8):
        return [
            {
                "content": "В документе упоминаются общие положения о развитии технологий ИИ.",
                "metadata": {"section_title": "Общие положения", "section_path": "пункт 10"},
                "source_path": "doc://policy",
                "source_type": "pdf",
                "rerank_score": 0.91,
                "distance": 0.09,
            },
        ]

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(fake_search)})())
    monkeypatch.setattr(rag_module, "ai_manager", type("Y", (), {"query": staticmethod(lambda prompt: prompt)})())

    point_chunk = DummyChunkRow(
        content=(
            "25. Целями развития искусственного интеллекта в Российской Федерации "
            "являются обеспечение роста благосостояния и качества жизни населения."
        ),
        metadata={"section_title": "IV. Цели и задачи", "section_path": "25"},
    )
    payload = RAGQuery(
        query="Какие цели развития искусственного интеллекта названы в пункте 25?",
        knowledge_base_id=1,
    )
    result = rag_query(payload, db=DummyDB(chunk_rows=[point_chunk]))

    assert "25. Целями развития искусственного интеллекта" in result.answer


def test_rag_query_factoid_uses_keyword_fallback_chunk(monkeypatch):
    from backend.api.routes import rag as rag_module

    def fake_search(query, knowledge_base_id=None, top_k=8):
        return [
            {
                "content": "В документе есть общий раздел о реализации стратегии.",
                "metadata": {"section_title": "Обзор", "section_path": "пункт 40"},
                "source_path": "doc://policy",
                "source_type": "pdf",
                "rerank_score": 0.88,
                "distance": 0.12,
            },
        ]

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(fake_search)})())
    monkeypatch.setattr(rag_module, "ai_manager", type("Y", (), {"query": staticmethod(lambda prompt: prompt)})())

    fact_chunk = DummyChunkRow(
        content=(
            "Решение о корректировке Стратегии принимается Президентом Российской Федерации "
            "не реже одного раза в 6 лет."
        ),
        metadata={"section_title": "Корректировка стратегии", "section_path": "пункт 50"},
    )
    payload = RAGQuery(
        query="Кто и как часто принимает решение о корректировке стратегии?",
        knowledge_base_id=1,
    )
    result = rag_query(payload, db=DummyDB(chunk_rows=[fact_chunk]))

    assert "принимается Президентом Российской Федерации" in result.answer
    assert "не реже одного раза в 6 лет" in result.answer


def test_rag_query_factoid_target_metric_with_year(monkeypatch):
    from backend.api.routes import rag as rag_module

    def fake_search(query, knowledge_base_id=None, top_k=8):
        return [
            {
                "content": "В документе есть общий раздел о реализации стратегии.",
                "metadata": {"section_title": "Обзор", "section_path": "пункт 40"},
                "source_path": "doc://policy",
                "source_type": "pdf",
                "rerank_score": 0.86,
                "distance": 0.14,
            },
        ]

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(fake_search)})())
    monkeypatch.setattr(rag_module, "ai_manager", type("Y", (), {"query": staticmethod(lambda prompt: prompt)})())

    metric_chunk = DummyChunkRow(
        content=(
            "Целевой ежегодный объем оказания услуг в области решений с использованием технологий "
            "искусственного интеллекта установлен на уровне не менее 60 млрд рублей к 2030 году."
        ),
        metadata={"section_title": "Целевые показатели", "section_path": "пункт 31"},
    )
    payload = RAGQuery(
        query="Какой целевой ежегодный объем услуг по решениям в области ИИ установлен на 2030 год?",
        knowledge_base_id=1,
    )
    result = rag_query(payload, db=DummyDB(chunk_rows=[metric_chunk]))

    assert "не менее 60 млрд рублей к 2030 году" in result.answer
