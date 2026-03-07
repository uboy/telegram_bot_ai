import json

import pytest

pytest.importorskip("fastapi")

from backend.api.routes.rag import _persist_retrieval_logs, rag_diagnostics
from backend.app import create_app
from shared.database import RetrievalCandidateLog, RetrievalQueryLog


class DummyQuery:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def filter_by(self, **kwargs):
        filtered = []
        for row in self._rows:
            if all(getattr(row, key, None) == value for key, value in kwargs.items()):
                filtered.append(row)
        self._rows = filtered
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class DummyDB:
    def __init__(self):
        self.retrieval_query_logs = []
        self.retrieval_candidate_logs = []

    def query(self, model):
        model_name = getattr(model, "__name__", "")
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


def test_persist_retrieval_logs_derives_trace_metrics():
    db = DummyDB()

    _persist_retrieval_logs(
        db=db,
        request_id="req-derived",
        query="What target is set for 2030?",
        knowledge_base_id=7,
        intent="FACTOID",
        hints={"retrieval_core_mode": "generalized"},
        filters={"source_types": ["pdf"]},
        total_candidates=3,
        total_selected=2,
        latency_ms=87,
        backend_name="qdrant",
        orchestrator_mode="legacy",
        candidates=[
            {
                "source_path": "doc://dense-1",
                "source_type": "pdf",
                "origin": "qdrant",
                "distance": -0.450000,
                "rerank_score": 0.910000,
                "rank_score": 0.880000,
                "content": "Dense candidate 1",
            },
            {
                "source_path": "doc://bm25-1",
                "source_type": "pdf",
                "origin": "bm25",
                "distance": 0.200000,
                "rank_score": 0.730000,
                "content": "BM25 candidate 1",
            },
            {
                "source_path": "doc://dense-2",
                "source_type": "pdf",
                "origin": "qdrant",
                "distance": -0.300000,
                "rerank_score": 0.620000,
                "rank_score": 0.610000,
                "content": "Dense candidate 2",
            },
        ],
    )

    first, second, third = db.retrieval_candidate_logs
    assert (first.origin, first.channel, first.channel_rank, first.fusion_rank, first.fusion_score, first.rerank_delta) == (
        "qdrant",
        "qdrant",
        1,
        1,
        "0.880000",
        "0.460000",
    )
    assert (second.origin, second.channel, second.channel_rank, second.fusion_rank, second.fusion_score, second.rerank_delta) == (
        "bm25",
        "bm25",
        1,
        2,
        "0.730000",
        None,
    )
    assert (third.origin, third.channel, third.channel_rank, third.fusion_rank, third.fusion_score, third.rerank_delta) == (
        "qdrant",
        "qdrant",
        2,
        3,
        "0.610000",
        "0.320000",
    )
    assert '"retrieval_core_mode": "generalized"' in (db.retrieval_query_logs[0].hints_json or "")


def test_rag_diagnostics_returns_strict_trace_fields_for_legacy_rows():
    db = DummyDB()
    db.retrieval_query_logs.append(
        RetrievalQueryLog(
            request_id="req-legacy",
            knowledge_base_id=2,
            query="Какие цели названы в документе?",
            intent="FACTOID",
            hints_json=json.dumps(
                {
                    "orchestrator_mode": "legacy",
                    "retrieval_core_mode": "generalized",
                },
                ensure_ascii=False,
            ),
            filters_json=json.dumps({}, ensure_ascii=False),
            total_candidates=1,
            total_selected=1,
            latency_ms=34,
            backend_name="qdrant",
            degraded_mode=False,
            degraded_reason=None,
        )
    )
    db.retrieval_candidate_logs.append(
        RetrievalCandidateLog(
            request_id="req-legacy",
            rank=1,
            source_path="doc://legacy",
            source_type="pdf",
            distance="-0.721500",
            rerank_score=None,
            origin=None,
            channel=None,
            channel_rank=None,
            fusion_rank=None,
            fusion_score=None,
            rerank_delta=None,
            metadata_json=json.dumps({"section_path": "25"}, ensure_ascii=False),
            content_preview="25. Цели развития...",
        )
    )

    result = rag_diagnostics("req-legacy", db=db)

    assert result.orchestrator_mode == "legacy"
    assert result.retrieval_core_mode == "generalized"
    assert result.candidates[0].origin == "unknown"
    assert result.candidates[0].channel == "unknown"
    assert result.candidates[0].channel_rank == 1
    assert result.candidates[0].fusion_rank == 1
    assert result.candidates[0].fusion_score == "0.721500"


def test_rag_diagnostics_openapi_schema_exposes_required_trace_fields():
    app = create_app()
    schema = app.openapi()

    response_schema = schema["components"]["schemas"]["RAGDiagnosticsResponse"]
    candidate_schema = schema["components"]["schemas"]["RAGDiagnosticsCandidate"]

    assert "retrieval_core_mode" in response_schema["properties"]
    assert {"origin", "channel", "channel_rank", "fusion_rank", "fusion_score"} <= set(
        candidate_schema.get("required") or []
    )
