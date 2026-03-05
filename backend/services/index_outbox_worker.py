from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from shared.database import IndexOutboxEvent, IndexSyncAudit, KnowledgeChunk, get_session
from shared.index_outbox_service import index_outbox_service
from shared.logging_config import logger
from shared.rag_system import rag_system
from backend.services.rag_retention_service import run_retention_once


_worker_started = False
_worker_lock = threading.Lock()


def _get_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _get_int_env(name: str, default: int, *, min_value: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(min_value, value)


def _get_float_env(name: str, default: float, *, min_value: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(min_value, value)


def _worker_config() -> Dict[str, Any]:
    return {
        "enabled": _get_bool_env("RAG_INDEX_OUTBOX_WORKER_ENABLED", True),
        "poll_interval_sec": _get_float_env("RAG_INDEX_OUTBOX_POLL_INTERVAL_SEC", 2.0, min_value=0.1),
        "batch_size": _get_int_env("RAG_INDEX_OUTBOX_BATCH_SIZE", 50, min_value=1),
        "max_attempts": _get_int_env("RAG_INDEX_OUTBOX_MAX_ATTEMPTS", 6, min_value=1),
        "retry_base_sec": _get_int_env("RAG_INDEX_OUTBOX_RETRY_BASE_SEC", 5, min_value=0),
        "retry_max_sec": _get_int_env("RAG_INDEX_OUTBOX_RETRY_MAX_SEC", 300, min_value=1),
        "drift_audit_interval_sec": _get_float_env("RAG_INDEX_DRIFT_AUDIT_INTERVAL_SEC", 300.0, min_value=5.0),
        "drift_max_kbs": _get_int_env("RAG_INDEX_DRIFT_MAX_KBS", 200, min_value=1),
        "drift_warning_ratio": _get_float_env("RAG_INDEX_DRIFT_WARNING_RATIO", 0.0005, min_value=0.0),
        "drift_critical_ratio": _get_float_env("RAG_INDEX_DRIFT_CRITICAL_RATIO", 0.001, min_value=0.0),
        "retention_enabled": _get_bool_env("RAG_RETENTION_ENABLED", True),
        "retention_interval_sec": _get_float_env("RAG_RETENTION_INTERVAL_SEC", 3600.0, min_value=60.0),
    }


def _get_qdrant_backend() -> Any | None:
    backend_name = (getattr(rag_system, "retrieval_backend", "") or "").strip().lower()
    backend = getattr(rag_system, "qdrant_backend", None)
    if backend_name != "qdrant":
        return None
    if backend is None or not getattr(backend, "enabled", False):
        return None
    return backend


def _safe_json_loads(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _retry_delay_sec(*, attempt_count: int, base_sec: int, max_sec: int) -> int:
    attempt = max(1, int(attempt_count or 1))
    delay = int(base_sec) * (2 ** max(0, attempt - 1))
    return max(0, min(int(max_sec), delay))


def _chunk_to_point(chunk: KnowledgeChunk) -> Dict[str, Any] | None:
    if not getattr(chunk, "embedding", None):
        return None
    try:
        vector_raw = json.loads(chunk.embedding)
    except Exception:
        return None
    if not isinstance(vector_raw, list) or not vector_raw:
        return None
    try:
        vector = [float(item) for item in vector_raw]
    except Exception:
        return None

    payload = {
        "kb_id": int(chunk.knowledge_base_id),
        "chunk_id": int(chunk.id),
        "source_type": chunk.source_type or "",
        "source_path": chunk.source_path or "",
        "content": chunk.content or "",
        "chunk_metadata": chunk.chunk_metadata or "{}",
    }
    return {
        "id": int(chunk.id),
        "vector": vector,
        "payload": payload,
    }


def _load_chunks_for_event(event: IndexOutboxEvent, payload: Dict[str, Any]) -> List[KnowledgeChunk]:
    source_type = str(payload.get("source_type") or "").strip().lower()
    source_path = str(payload.get("source_path") or "").strip()

    with get_session() as session:
        query = session.query(KnowledgeChunk).filter(
            KnowledgeChunk.knowledge_base_id == int(event.knowledge_base_id),
            KnowledgeChunk.is_deleted.is_(False),
            KnowledgeChunk.embedding.isnot(None),
        )

        if getattr(event, "document_id", None) is not None:
            query = query.filter(KnowledgeChunk.document_id == int(event.document_id))
        if getattr(event, "version", None) is not None:
            query = query.filter(KnowledgeChunk.version == int(event.version))

        if source_type:
            if source_type == "codebase":
                if not source_path:
                    return []
                query = query.filter(KnowledgeChunk.source_type == "code")
                query = query.filter(KnowledgeChunk.source_path.like(f"{source_path}::%"))
            elif source_type in {"wiki", "wiki_git", "wiki_zip"}:
                if source_path:
                    query = query.filter(KnowledgeChunk.source_path.like(f"{source_path}%"))
            else:
                query = query.filter(KnowledgeChunk.source_type == source_type)
                if source_path:
                    query = query.filter(KnowledgeChunk.source_path == source_path)
        elif source_path:
            query = query.filter(KnowledgeChunk.source_path == source_path)

        return query.all()


def _upsert_chunks_to_qdrant(chunks: List[KnowledgeChunk]) -> int:
    backend = _get_qdrant_backend()
    if backend is None:
        raise RuntimeError("Qdrant backend is not available")

    points: List[Dict[str, Any]] = []
    for chunk in chunks:
        point = _chunk_to_point(chunk)
        if point:
            points.append(point)

    if not points:
        return 0

    backend.ensure_collection(len(points[0]["vector"]))
    total = 0
    for idx in range(0, len(points), 128):
        batch = points[idx:idx + 128]
        total += int(backend.upsert_points(batch, wait=True) or 0)
    return total


def _process_upsert_event(event: IndexOutboxEvent, payload: Dict[str, Any]) -> int:
    chunks = _load_chunks_for_event(event, payload)
    if not chunks:
        logger.info(
            "Outbox UPSERT event has no matching chunks: event_id=%s kb_id=%s",
            event.event_id,
            event.knowledge_base_id,
        )
        return 0
    upserted = _upsert_chunks_to_qdrant(chunks)
    return upserted


def _process_delete_source_event(event: IndexOutboxEvent, payload: Dict[str, Any]) -> None:
    backend = _get_qdrant_backend()
    if backend is None:
        raise RuntimeError("Qdrant backend is not available")
    backend.delete_by_filter(
        kb_id=int(event.knowledge_base_id),
        source_type=(str(payload.get("source_type") or "").strip() or None),
        source_path=(str(payload.get("source_path") or "").strip() or None),
    )


def _process_delete_kb_event(event: IndexOutboxEvent) -> None:
    backend = _get_qdrant_backend()
    if backend is None:
        raise RuntimeError("Qdrant backend is not available")
    backend.delete_kb(int(event.knowledge_base_id))


def process_pending_events_once(*, limit: Optional[int] = None) -> int:
    cfg = _worker_config()
    claim_limit = int(limit or cfg["batch_size"])
    events = index_outbox_service.claim_pending(limit=claim_limit)
    if not events:
        return 0

    handled = 0
    for event in events:
        payload = _safe_json_loads(getattr(event, "payload_json", None))
        operation = (getattr(event, "operation", "") or "").strip().upper()
        try:
            if operation == "UPSERT":
                _process_upsert_event(event, payload)
            elif operation == "DELETE_SOURCE":
                _process_delete_source_event(event, payload)
            elif operation == "DELETE_KB":
                _process_delete_kb_event(event)
            else:
                raise ValueError(f"Unsupported outbox operation: {operation}")

            index_outbox_service.mark_processed(event_id=event.event_id)
            handled += 1
        except Exception as exc:  # noqa: BLE001
            attempts = int(getattr(event, "attempt_count", 0) or 0)
            max_attempts = int(cfg["max_attempts"])
            if attempts >= max_attempts:
                index_outbox_service.mark_dead(event_id=event.event_id, error=str(exc))
                logger.error(
                    "Outbox event moved to dead-letter: event_id=%s op=%s attempts=%s error=%s",
                    event.event_id,
                    operation,
                    attempts,
                    exc,
                )
            else:
                delay_sec = _retry_delay_sec(
                    attempt_count=attempts,
                    base_sec=int(cfg["retry_base_sec"]),
                    max_sec=int(cfg["retry_max_sec"]),
                )
                index_outbox_service.mark_failed(
                    event_id=event.event_id,
                    error=str(exc),
                    retry_delay_sec=delay_sec,
                )
                logger.warning(
                    "Outbox event retry scheduled: event_id=%s op=%s attempts=%s delay=%ss error=%s",
                    event.event_id,
                    operation,
                    attempts,
                    delay_sec,
                    exc,
                )
    return handled


def _list_active_kb_ids_with_embeddings(*, max_kbs: Optional[int] = None) -> List[int]:
    with get_session() as session:
        rows = (
            session.query(KnowledgeChunk.knowledge_base_id)
            .filter(
                KnowledgeChunk.is_deleted.is_(False),
                KnowledgeChunk.embedding.isnot(None),
            )
            .distinct()
            .all()
        )
    kb_ids = sorted({int(row[0]) for row in rows if row and row[0] is not None})
    if max_kbs is not None and max_kbs > 0:
        return kb_ids[:max_kbs]
    return kb_ids


def _count_expected_chunks(kb_id: int) -> int:
    with get_session() as session:
        count = (
            session.query(KnowledgeChunk)
            .filter(
                KnowledgeChunk.knowledge_base_id == int(kb_id),
                KnowledgeChunk.is_deleted.is_(False),
                KnowledgeChunk.embedding.isnot(None),
            )
            .count()
        )
    return int(count or 0)


def _record_sync_audit(
    *,
    kb_id: int,
    expected_active_chunks: int,
    indexed_chunks: int,
    drift_ratio: float,
    status: str,
    details: Dict[str, Any],
) -> None:
    with get_session() as session:
        session.add(
            IndexSyncAudit(
                knowledge_base_id=int(kb_id),
                expected_active_chunks=max(0, int(expected_active_chunks)),
                indexed_chunks=max(0, int(indexed_chunks)),
                drift_ratio=max(0.0, float(drift_ratio)),
                status=(status or "ok")[:20],
                details_json=json.dumps(details, ensure_ascii=False, default=str),
                created_at=datetime.now(timezone.utc),
            )
        )


def run_index_drift_audit_once(*, max_kbs: Optional[int] = None) -> int:
    backend = _get_qdrant_backend()
    if backend is None:
        return 0

    cfg = _worker_config()
    warning_ratio = float(cfg["drift_warning_ratio"])
    critical_ratio = float(cfg["drift_critical_ratio"])
    if critical_ratio < warning_ratio:
        critical_ratio = warning_ratio

    kb_ids = _list_active_kb_ids_with_embeddings(max_kbs=max_kbs or int(cfg["drift_max_kbs"]))
    audits_written = 0
    for kb_id in kb_ids:
        expected = _count_expected_chunks(kb_id)
        indexed = int(backend.count_points(kb_id=int(kb_id)) or 0)
        drift = abs(indexed - expected) / max(1, expected)
        if drift > critical_ratio:
            status = "critical"
        elif drift > warning_ratio:
            status = "warning"
        else:
            status = "ok"
        _record_sync_audit(
            kb_id=kb_id,
            expected_active_chunks=expected,
            indexed_chunks=indexed,
            drift_ratio=drift,
            status=status,
            details={
                "delta": int(indexed - expected),
                "backend": "qdrant",
                "warning_ratio": warning_ratio,
                "critical_ratio": critical_ratio,
            },
        )
        audits_written += 1
    return audits_written


def _worker_loop() -> None:
    cfg = _worker_config()
    logger.info(
        "Index outbox worker loop started: batch_size=%s poll=%.2fs",
        cfg["batch_size"],
        cfg["poll_interval_sec"],
    )
    next_drift_audit_at = time.monotonic() + float(cfg["drift_audit_interval_sec"])
    next_retention_at = time.monotonic() + float(cfg["retention_interval_sec"])

    while True:
        handled = 0
        try:
            handled = process_pending_events_once(limit=int(cfg["batch_size"]))
        except Exception as exc:  # noqa: BLE001
            logger.error("Index outbox worker iteration failed: %s", exc, exc_info=True)

        now = time.monotonic()
        if now >= next_drift_audit_at:
            try:
                audited = run_index_drift_audit_once(max_kbs=int(cfg["drift_max_kbs"]))
                if audited:
                    logger.info("Index drift audit completed: kb_count=%s", audited)
            except Exception as exc:  # noqa: BLE001
                logger.error("Index drift audit iteration failed: %s", exc, exc_info=True)
            next_drift_audit_at = now + float(cfg["drift_audit_interval_sec"])

        if bool(cfg["retention_enabled"]) and now >= next_retention_at:
            try:
                summary = run_retention_once()
                logger.info("Retention cleanup completed: %s", summary)
            except Exception as exc:  # noqa: BLE001
                logger.error("Retention cleanup iteration failed: %s", exc, exc_info=True)
            next_retention_at = now + float(cfg["retention_interval_sec"])

        sleep_sec = 0.1 if handled > 0 else float(cfg["poll_interval_sec"])
        time.sleep(max(0.05, sleep_sec))


def start_index_outbox_worker() -> None:
    global _worker_started

    cfg = _worker_config()
    if not bool(cfg["enabled"]):
        logger.info("Index outbox worker disabled by RAG_INDEX_OUTBOX_WORKER_ENABLED")
        return
    if _get_qdrant_backend() is None:
        logger.info("Index outbox worker not started: retrieval backend is not qdrant")
        return

    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(target=_worker_loop, name="index-outbox-worker", daemon=True)
        thread.start()
        _worker_started = True
        logger.info("Index outbox worker started")
