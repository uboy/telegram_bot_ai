from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import and_, or_

from shared.database import (
    Document,
    DocumentVersion,
    IndexSyncAudit,
    KnowledgeChunk,
    RAGEvalResult,
    RAGEvalRun,
    RetentionDeletionAudit,
    RetrievalCandidateLog,
    RetrievalQueryLog,
    get_session,
)
from shared.logging_config import logger


def _int_env(name: str, default: int, *, min_value: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(min_value, value)


def _retention_days() -> Dict[str, int]:
    return {
        "query_logs": _int_env("RAG_RETENTION_QUERY_LOG_DAYS", 30, min_value=1),
        "doc_versions": _int_env("RAG_RETENTION_DOC_OLD_VERSION_DAYS", 30, min_value=1),
        "eval": _int_env("RAG_RETENTION_EVAL_DAYS", 90, min_value=1),
        "drift_audit": _int_env("RAG_RETENTION_DRIFT_AUDIT_DAYS", 90, min_value=1),
        "retention_audit": _int_env("RAG_RETENTION_AUDIT_DAYS", 365, min_value=1),
    }


def _add_retention_audit(
    *,
    table_name: str,
    policy_name: str,
    rows_deleted: int,
    deleted_before: datetime,
    details: Optional[Dict[str, Any]] = None,
    status: str = "ok",
) -> None:
    with get_session() as session:
        session.add(
            RetentionDeletionAudit(
                table_name=table_name[:80],
                policy_name=policy_name[:80],
                rows_deleted=max(0, int(rows_deleted or 0)),
                deleted_before=deleted_before,
                status=(status or "ok")[:20],
                details_json=json.dumps(details or {}, ensure_ascii=False, default=str),
            )
        )


def _purge_query_logs(*, cutoff: datetime) -> Dict[str, int]:
    deleted_queries = 0
    deleted_candidates = 0
    with get_session() as session:
        request_ids = [
            str(row[0])
            for row in session.query(RetrievalQueryLog.request_id)
            .filter(RetrievalQueryLog.created_at < cutoff)
            .all()
            if row and row[0]
        ]
        if request_ids:
            deleted_candidates = int(
                session.query(RetrievalCandidateLog)
                .filter(RetrievalCandidateLog.request_id.in_(request_ids))
                .delete(synchronize_session=False)
                or 0
            )
            deleted_queries = int(
                session.query(RetrievalQueryLog)
                .filter(RetrievalQueryLog.request_id.in_(request_ids))
                .delete(synchronize_session=False)
                or 0
            )
    _add_retention_audit(
        table_name="retrieval_query_logs",
        policy_name="query_logs_30d",
        rows_deleted=deleted_queries,
        deleted_before=cutoff,
        details={"candidates_deleted": deleted_candidates},
    )
    _add_retention_audit(
        table_name="retrieval_candidate_logs",
        policy_name="query_logs_30d",
        rows_deleted=deleted_candidates,
        deleted_before=cutoff,
        details={"queries_deleted": deleted_queries},
    )
    return {
        "retrieval_query_logs": deleted_queries,
        "retrieval_candidate_logs": deleted_candidates,
    }


def _purge_eval_logs(*, cutoff: datetime) -> Dict[str, int]:
    deleted_results = 0
    deleted_runs = 0
    with get_session() as session:
        deleted_results = int(
            session.query(RAGEvalResult)
            .filter(RAGEvalResult.created_at < cutoff)
            .delete(synchronize_session=False)
            or 0
        )
        deleted_runs = int(
            session.query(RAGEvalRun)
            .filter(RAGEvalRun.created_at < cutoff)
            .delete(synchronize_session=False)
            or 0
        )
    _add_retention_audit(
        table_name="rag_eval_results",
        policy_name="eval_90d",
        rows_deleted=deleted_results,
        deleted_before=cutoff,
    )
    _add_retention_audit(
        table_name="rag_eval_runs",
        policy_name="eval_90d",
        rows_deleted=deleted_runs,
        deleted_before=cutoff,
        details={"results_deleted": deleted_results},
    )
    return {
        "rag_eval_results": deleted_results,
        "rag_eval_runs": deleted_runs,
    }


def _purge_drift_audit(*, cutoff: datetime) -> int:
    deleted = 0
    with get_session() as session:
        deleted = int(
            session.query(IndexSyncAudit)
            .filter(IndexSyncAudit.created_at < cutoff)
            .delete(synchronize_session=False)
            or 0
        )
    _add_retention_audit(
        table_name="index_sync_audit",
        policy_name="drift_audit_90d",
        rows_deleted=deleted,
        deleted_before=cutoff,
    )
    return deleted


def _purge_old_document_versions(*, cutoff: datetime) -> Dict[str, int]:
    deleted_chunks = 0
    deleted_versions = 0

    with get_session() as session:
        stale_chunk_ids = [
            int(row[0])
            for row in session.query(KnowledgeChunk.id)
            .join(Document, KnowledgeChunk.document_id == Document.id, isouter=True)
            .filter(
                KnowledgeChunk.created_at < cutoff,
                or_(
                    KnowledgeChunk.is_deleted.is_(True),
                    and_(
                        KnowledgeChunk.document_id.isnot(None),
                        Document.current_version.isnot(None),
                        KnowledgeChunk.version.isnot(None),
                        KnowledgeChunk.version < Document.current_version,
                    ),
                ),
            )
            .all()
            if row and row[0] is not None
        ]
        if stale_chunk_ids:
            deleted_chunks = int(
                session.query(KnowledgeChunk)
                .filter(KnowledgeChunk.id.in_(stale_chunk_ids))
                .delete(synchronize_session=False)
                or 0
            )

        stale_version_ids = [
            int(row[0])
            for row in session.query(DocumentVersion.id)
            .join(Document, DocumentVersion.document_id == Document.id)
            .filter(
                DocumentVersion.created_at < cutoff,
                Document.current_version.isnot(None),
                DocumentVersion.version < Document.current_version,
            )
            .all()
            if row and row[0] is not None
        ]
        if stale_version_ids:
            deleted_versions = int(
                session.query(DocumentVersion)
                .filter(DocumentVersion.id.in_(stale_version_ids))
                .delete(synchronize_session=False)
                or 0
            )

    _add_retention_audit(
        table_name="knowledge_chunks",
        policy_name="old_versions_30d",
        rows_deleted=deleted_chunks,
        deleted_before=cutoff,
    )
    _add_retention_audit(
        table_name="document_versions",
        policy_name="old_versions_30d",
        rows_deleted=deleted_versions,
        deleted_before=cutoff,
        details={"chunks_deleted": deleted_chunks},
    )
    return {
        "knowledge_chunks": deleted_chunks,
        "document_versions": deleted_versions,
    }


def _purge_retention_audit(*, cutoff: datetime) -> int:
    deleted = 0
    with get_session() as session:
        deleted = int(
            session.query(RetentionDeletionAudit)
            .filter(RetentionDeletionAudit.created_at < cutoff)
            .delete(synchronize_session=False)
            or 0
        )
    _add_retention_audit(
        table_name="retention_deletion_audit",
        policy_name="retention_audit_365d",
        rows_deleted=deleted,
        deleted_before=cutoff,
    )
    return deleted


def run_retention_once(*, now: Optional[datetime] = None) -> Dict[str, int]:
    current = now or datetime.now(timezone.utc)
    days = _retention_days()

    cutoff_query = current - timedelta(days=int(days["query_logs"]))
    cutoff_doc_versions = current - timedelta(days=int(days["doc_versions"]))
    cutoff_eval = current - timedelta(days=int(days["eval"]))
    cutoff_drift = current - timedelta(days=int(days["drift_audit"]))
    cutoff_audit = current - timedelta(days=int(days["retention_audit"]))

    summary: Dict[str, int] = {}
    summary.update(_purge_query_logs(cutoff=cutoff_query))
    summary.update(_purge_old_document_versions(cutoff=cutoff_doc_versions))
    summary.update(_purge_eval_logs(cutoff=cutoff_eval))
    summary["index_sync_audit"] = _purge_drift_audit(cutoff=cutoff_drift)
    summary["retention_deletion_audit"] = _purge_retention_audit(cutoff=cutoff_audit)

    total_deleted = sum(max(0, int(v or 0)) for v in summary.values())
    logger.info(
        "Retention run completed: total_deleted=%s details=%s",
        total_deleted,
        summary,
    )
    summary["total_deleted"] = total_deleted
    return summary
