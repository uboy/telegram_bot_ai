from datetime import datetime, timezone

from backend.services import rag_retention_service as retention_module


def test_retention_days_from_env(monkeypatch):
    monkeypatch.setenv("RAG_RETENTION_QUERY_LOG_DAYS", "31")
    monkeypatch.setenv("RAG_RETENTION_DOC_OLD_VERSION_DAYS", "32")
    monkeypatch.setenv("RAG_RETENTION_EVAL_DAYS", "91")
    monkeypatch.setenv("RAG_RETENTION_DRIFT_AUDIT_DAYS", "92")
    monkeypatch.setenv("RAG_RETENTION_AUDIT_DAYS", "366")

    days = retention_module._retention_days()

    assert days["query_logs"] == 31
    assert days["doc_versions"] == 32
    assert days["eval"] == 91
    assert days["drift_audit"] == 92
    assert days["retention_audit"] == 366


def test_run_retention_once_aggregates_summary(monkeypatch):
    monkeypatch.setattr(
        retention_module,
        "_purge_query_logs",
        lambda cutoff: {"retrieval_query_logs": 2, "retrieval_candidate_logs": 4},  # noqa: ARG005
    )
    monkeypatch.setattr(
        retention_module,
        "_purge_old_document_versions",
        lambda cutoff: {"knowledge_chunks": 3, "document_versions": 1},  # noqa: ARG005
    )
    monkeypatch.setattr(
        retention_module,
        "_purge_eval_logs",
        lambda cutoff: {"rag_eval_results": 5, "rag_eval_runs": 2},  # noqa: ARG005
    )
    monkeypatch.setattr(retention_module, "_purge_drift_audit", lambda cutoff: 6)  # noqa: ARG005
    monkeypatch.setattr(retention_module, "_purge_retention_audit", lambda cutoff: 7)  # noqa: ARG005

    summary = retention_module.run_retention_once(now=datetime(2026, 3, 5, tzinfo=timezone.utc))

    assert summary["retrieval_query_logs"] == 2
    assert summary["retrieval_candidate_logs"] == 4
    assert summary["knowledge_chunks"] == 3
    assert summary["document_versions"] == 1
    assert summary["rag_eval_results"] == 5
    assert summary["rag_eval_runs"] == 2
    assert summary["index_sync_audit"] == 6
    assert summary["retention_deletion_audit"] == 7
    assert summary["total_deleted"] == 30
