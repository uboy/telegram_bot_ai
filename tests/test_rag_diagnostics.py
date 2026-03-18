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
    import shared.config as shared_config

    monkeypatch.setattr(shared_config, "RAG_ORCHESTRATOR_V4", False, raising=False)
    monkeypatch.setattr(shared_config, "RAG_LEGACY_QUERY_HEURISTICS", False, raising=False)

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
    assert "\"orchestrator_mode\": \"legacy\"" in (db.retrieval_query_logs[0].hints_json or "")
    assert "\"retrieval_core_mode\": \"generalized\"" in (db.retrieval_query_logs[0].hints_json or "")


def test_rag_query_persists_legacy_retrieval_core_mode_hint(monkeypatch):
    from backend.api.routes import rag as rag_module
    import shared.config as shared_config

    monkeypatch.setattr(shared_config, "RAG_ORCHESTRATOR_V4", False, raising=False)
    monkeypatch.setattr(shared_config, "RAG_LEGACY_QUERY_HEURISTICS", True, raising=False)

    def fake_search(query, knowledge_base_id=None, top_k=8):  # noqa: ARG001
        return [
            {
                "content": "Здесь описан порядок выполнения команды sync и build.",
                "metadata": {"section_title": "How to build", "section_path": "setup"},
                "source_path": "doc://build",
                "source_type": "md",
                "rerank_score": 0.88,
                "distance": 0.12,
                "origin": "bm25",
            }
        ]

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(fake_search), "retrieval_backend": "qdrant"})())
    monkeypatch.setattr(rag_module, "ai_manager", type("Y", (), {"query": staticmethod(lambda prompt: "Ответ")})())

    db = DummyDB()
    payload = RAGQuery(query="How to build the project?", knowledge_base_id=1)
    rag_query(payload, db=db)

    assert len(db.retrieval_query_logs) == 1
    assert "\"retrieval_core_mode\": \"legacy_heuristic\"" in (db.retrieval_query_logs[0].hints_json or "")


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
            hints_json=json.dumps({"point_numbers": ["25"], "orchestrator_mode": "v4"}, ensure_ascii=False),
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
            metadata_json=json.dumps(
                {
                    "section_path": "25",
                    "_diag_context_selected": True,
                    "_diag_context_rank": 1,
                    "_diag_context_reason": "primary",
                    "_diag_context_anchor_rank": 1,
                },
                ensure_ascii=False,
            ),
            content_preview="25. Целями развития искусственного интеллекта...",
        )
    )

    result = rag_diagnostics("req-1", db=db)

    assert result.request_id == "req-1"
    assert result.intent == "FACTOID"
    assert result.orchestrator_mode == "v4"
    assert result.total_candidates == 5
    assert result.degraded_mode is True
    assert result.degraded_reason == "qdrant_unavailable_or_empty"
    assert len(result.candidates) == 1
    assert result.candidates[0].metadata == {"section_path": "25"}
    assert result.candidates[0].channel == "qdrant"
    assert result.candidates[0].fusion_rank == 1
    assert result.candidates[0].included_in_context is True
    assert result.candidates[0].context_rank == 1
    assert result.candidates[0].context_reason == "primary"
    assert result.candidates[0].context_anchor_rank == 1
    assert result.candidates[0].family_key is None
    assert result.candidates[0].family_rank is None


def test_rag_query_diagnostics_include_context_support_rows(monkeypatch):
    from backend.api.routes import rag as rag_module
    import shared.config as shared_config

    monkeypatch.setattr(shared_config, "RAG_ORCHESTRATOR_V4", False, raising=False)
    monkeypatch.setattr(shared_config, "RAG_LEGACY_QUERY_HEURISTICS", False, raising=False)

    def fake_search(query, knowledge_base_id=None, top_k=8):  # noqa: ARG001
        return [
            {
                "content": "Step 2 run `repo sync -c -j 8` from the repository root.",
                "metadata": {
                    "doc_title": "Sync Guide",
                    "section_title": "Sync and Build",
                    "section_path": "Sync Guide > Sync and Build",
                    "section_path_norm": "sync guide > sync and build",
                    "chunk_no": 2,
                    "chunk_kind": "text",
                },
                "source_path": "doc://guide",
                "source_type": "markdown",
                "rerank_score": 0.95,
                "distance": 0.05,
                "origin": "qdrant",
            }
        ]

    doc_chunks = {
        "doc://guide": [
            {
                "id": 11,
                "content": "Step 1 install prerequisites and initialize the repo tool.",
                "metadata": {
                    "doc_title": "Sync Guide",
                    "section_title": "Sync and Build",
                    "section_path": "Sync Guide > Sync and Build",
                    "section_path_norm": "sync guide > sync and build",
                    "chunk_no": 1,
                    "chunk_kind": "text",
                },
                "source_path": "doc://guide",
                "source_type": "markdown",
            },
            {
                "id": 12,
                "content": "Step 2 run `repo sync -c -j 8` from the repository root.",
                "metadata": {
                    "doc_title": "Sync Guide",
                    "section_title": "Sync and Build",
                    "section_path": "Sync Guide > Sync and Build",
                    "section_path_norm": "sync guide > sync and build",
                    "chunk_no": 2,
                    "chunk_kind": "text",
                },
                "source_path": "doc://guide",
                "source_type": "markdown",
            },
        ]
    }

    monkeypatch.setattr(
        rag_module,
        "rag_system",
        type("X", (), {"search": staticmethod(fake_search), "retrieval_backend": "qdrant"})(),
    )
    monkeypatch.setattr(rag_module, "ai_manager", type("Y", (), {"query": staticmethod(lambda prompt: "Ответ")})())
    monkeypatch.setattr(
        rag_module,
        "_load_doc_chunks_for_context",
        lambda db, doc_id, kb_id=None: list(doc_chunks.get(doc_id, [])),  # noqa: ARG005
    )

    db = DummyDB()
    result = rag_query(RAGQuery(query="How do I sync the repo?", knowledge_base_id=1), db=db)
    diagnostics = rag_diagnostics(result.request_id, db=db)

    assert [candidate.source_path for candidate in diagnostics.candidates] == ["doc://guide", "doc://guide"]
    assert diagnostics.candidates[0].origin == "qdrant"
    assert diagnostics.candidates[0].included_in_context is True
    assert diagnostics.candidates[0].context_rank == 1
    assert diagnostics.candidates[0].context_reason == "primary"
    assert diagnostics.candidates[0].context_anchor_rank == 1
    assert diagnostics.candidates[0].family_key == "doc://guide::section:sync guide > sync and build"
    assert diagnostics.candidates[0].family_rank == 1
    assert diagnostics.candidates[1].origin == "context_support"
    assert diagnostics.candidates[1].channel == "context_support"
    assert diagnostics.candidates[1].included_in_context is True
    assert diagnostics.candidates[1].context_rank == 2
    assert diagnostics.candidates[1].context_reason == "adjacent_prev"
    assert diagnostics.candidates[1].context_anchor_rank == 1
    assert diagnostics.candidates[1].family_key == "doc://guide::section:sync guide > sync and build"
    assert diagnostics.candidates[1].family_rank == 1
    assert diagnostics.candidates[1].metadata.get("section_path") == "Sync Guide > Sync and Build"
    assert "_diag_context_selected" not in (diagnostics.candidates[1].metadata or {})


def test_rag_query_diagnostics_keep_context_support_with_many_retrieval_candidates(monkeypatch):
    from backend.api.routes import rag as rag_module
    import shared.config as shared_config

    monkeypatch.setattr(shared_config, "RAG_ORCHESTRATOR_V4", False, raising=False)
    monkeypatch.setattr(shared_config, "RAG_LEGACY_QUERY_HEURISTICS", False, raising=False)

    retrieval_rows = []
    for idx in range(1, 26):
        retrieval_rows.append(
            {
                "content": f"Guide step {idx}",
                "metadata": {
                    "doc_title": "Long Guide",
                    "section_title": "Section",
                    "section_path": "Long Guide > Section",
                    "section_path_norm": "long guide > section",
                    "chunk_no": idx * 10,
                    "chunk_kind": "text",
                },
                "source_path": f"doc://guide-{idx}",
                "source_type": "markdown",
                "rerank_score": 1.0 - (idx * 0.01),
                "distance": 0.01 * idx,
                "origin": "qdrant",
            }
        )

    def fake_search(query, knowledge_base_id=None, top_k=8):  # noqa: ARG001
        return retrieval_rows

    doc_chunks = {
        "doc://guide-1": [
            {
                "id": 101,
                "content": "Guide step 1",
                "metadata": {
                    "doc_title": "Long Guide",
                    "section_title": "Section",
                    "section_path": "Long Guide > Section",
                    "section_path_norm": "long guide > section",
                    "chunk_no": 10,
                    "chunk_kind": "text",
                },
                "source_path": "doc://guide-1",
                "source_type": "markdown",
            },
            {
                "id": 102,
                "content": "Guide step 1 supporting detail",
                "metadata": {
                    "doc_title": "Long Guide",
                    "section_title": "Section",
                    "section_path": "Long Guide > Section",
                    "section_path_norm": "long guide > section",
                    "chunk_no": 11,
                    "chunk_kind": "text",
                },
                "source_path": "doc://guide-1",
                "source_type": "markdown",
            },
        ],
    }

    monkeypatch.setattr(
        rag_module,
        "rag_system",
        type("X", (), {"search": staticmethod(fake_search), "retrieval_backend": "qdrant"})(),
    )
    monkeypatch.setattr(rag_module, "ai_manager", type("Y", (), {"query": staticmethod(lambda prompt: "Ответ")})())
    monkeypatch.setattr(
        rag_module,
        "_load_doc_chunks_for_context",
        lambda db, doc_id, kb_id=None: list(doc_chunks.get(doc_id, [])),  # noqa: ARG005
    )

    db = DummyDB()
    result = rag_query(RAGQuery(query="Explain step 1", knowledge_base_id=1), db=db)
    diagnostics = rag_diagnostics(result.request_id, db=db)

    assert len(diagnostics.candidates) == 20
    assert any(candidate.origin == "context_support" for candidate in diagnostics.candidates)
    support_candidate = next(candidate for candidate in diagnostics.candidates if candidate.origin == "context_support")
    assert support_candidate.included_in_context is True
    assert support_candidate.context_reason == "adjacent_next"
    assert support_candidate.context_anchor_rank == 1
    assert support_candidate.context_rank is not None


def test_rag_diagnostics_not_found():
    with pytest.raises(HTTPException) as exc:
        rag_diagnostics("missing", db=DummyDB())
    assert exc.value.status_code == 404
