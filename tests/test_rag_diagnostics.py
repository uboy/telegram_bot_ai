import json

import pytest

pytest.importorskip("fastapi")

from fastapi import HTTPException

from backend.api.routes.rag import rag_query, rag_diagnostics
from backend.schemas.rag import RAGQuery
from shared.database import RetrievalQueryLog, RetrievalCandidateLog


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
        self._rows = list(rows or [])
        self._limit = None

    def filter_by(self, **kwargs):
        filtered = []
        for row in self._rows:
            ok = True
            for key, value in kwargs.items():
                if getattr(row, key, None) != value:
                    ok = False
                    break
            if ok:
                filtered.append(row)
        self._rows = filtered
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
        return None


class DummyDB:
    def __init__(self):
        self.retrieval_query_logs = []
        self.retrieval_candidate_logs = []

    def query(self, model):
        model_name = getattr(model, "__name__", "")
        if model_name == "KnowledgeBase":
            return DummyQuery(rows=[DummyKB()])
        if model_name == "RetrievalQueryLog":
            return DummyQuery(rows=self.retrieval_query_logs)
        if model_name == "RetrievalCandidateLog":
            return DummyQuery(rows=self.retrieval_candidate_logs)
        return DummyQuery()

    def add(self, obj):
        model_name = obj.__class__.__name__
        if model_name == "RetrievalQueryLog":
            self.retrieval_query_logs.append(obj)
        elif model_name == "RetrievalCandidateLog":
            self.retrieval_candidate_logs.append(obj)

    def commit(self):
        return None

    def rollback(self):
        return None


def test_rag_query_persists_request_id_and_retrieval_logs(monkeypatch):
    from backend.api.routes import rag as rag_module

    def fake_search(query, knowledge_base_id=None, top_k=8):  # noqa: ARG001
        return [
            {
                "content": (
                    "Разметка данных - этап обработки данных, при котором данным "
                    "присваиваются идентификаторы."
                ),
                "metadata": {"section_title": "Термины", "section_path": "пункт 2"},
                "source_path": "doc://glossary",
                "source_type": "pdf",
                "rerank_score": 0.93,
                "distance": 0.07,
                "origin": "qdrant",
            }
        ]

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(fake_search), "retrieval_backend": "qdrant"})())
    monkeypatch.setattr(rag_module, "ai_manager", type("Y", (), {"query": staticmethod(lambda prompt: "Короткий ответ")})())

    db = DummyDB()
    payload = RAGQuery(query="Как в документе определяется разметка данных?", knowledge_base_id=1)
    result = rag_query(payload, db=db)

    assert result.request_id
    assert result.answer == "Короткий ответ"
    assert len(db.retrieval_query_logs) == 1
    assert len(db.retrieval_candidate_logs) >= 1
    assert db.retrieval_query_logs[0].request_id == result.request_id
    assert db.retrieval_query_logs[0].backend_name == "qdrant"
    assert db.retrieval_query_logs[0].degraded_mode is False


def test_rag_query_marks_degraded_on_qdrant_fallback(monkeypatch):
    from backend.api.routes import rag as rag_module

    def fake_search(query, knowledge_base_id=None, top_k=8):  # noqa: ARG001
        return [
            {
                "content": "Найдено только через keyword fallback.",
                "metadata": {"section_title": "Fallback"},
                "source_path": "doc://fallback",
                "source_type": "pdf",
                "rerank_score": 0.81,
                "distance": 0.2,
                "origin": "bm25",
            }
        ]

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(fake_search), "retrieval_backend": "qdrant"})())
    monkeypatch.setattr(rag_module, "ai_manager", type("Y", (), {"query": staticmethod(lambda prompt: "Ответ")})())

    db = DummyDB()
    payload = RAGQuery(query="вопрос", knowledge_base_id=1)
    rag_query(payload, db=db)

    assert len(db.retrieval_query_logs) == 1
    assert db.retrieval_query_logs[0].degraded_mode is True
    assert db.retrieval_query_logs[0].degraded_reason == "qdrant_unavailable_or_empty"


def test_rag_diagnostics_returns_candidates():
    db = DummyDB()
    db.retrieval_query_logs.append(
        RetrievalQueryLog(
            request_id="req-1",
            knowledge_base_id=1,
            query="Какие цели развития ИИ названы в пункте 25?",
            intent="FACTOID",
            hints_json=json.dumps({"point_numbers": ["25"]}, ensure_ascii=False),
            filters_json=json.dumps({"source_types": ["pdf"]}, ensure_ascii=False),
            total_candidates=5,
            total_selected=2,
            latency_ms=123,
            backend_name="qdrant",
            degraded_mode=True,
            degraded_reason="qdrant_unavailable_or_empty",
        )
    )
    db.retrieval_candidate_logs.append(
        RetrievalCandidateLog(
            request_id="req-1",
            rank=1,
            source_path="doc://policy",
            source_type="pdf",
            distance="-0.721500",
            rerank_score="0.912300",
            origin="qdrant",
            channel="qdrant",
            channel_rank=1,
            fusion_rank=1,
            fusion_score="0.981200",
            rerank_delta="0.034100",
            metadata_json=json.dumps({"section_path": "25"}, ensure_ascii=False),
            content_preview="25. Целями развития искусственного интеллекта...",
        )
    )

    result = rag_diagnostics("req-1", db=db)

    assert result.request_id == "req-1"
    assert result.intent == "FACTOID"
    assert result.total_candidates == 5
    assert result.degraded_mode is True
    assert result.degraded_reason == "qdrant_unavailable_or_empty"
    assert len(result.candidates) == 1
    assert result.candidates[0].metadata == {"section_path": "25"}
    assert result.candidates[0].channel == "qdrant"
    assert result.candidates[0].fusion_rank == 1


def test_rag_diagnostics_not_found():
    with pytest.raises(HTTPException) as exc:
        rag_diagnostics("missing", db=DummyDB())
    assert exc.value.status_code == 404
