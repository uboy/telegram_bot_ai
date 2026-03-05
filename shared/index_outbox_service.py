import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from shared.database import IndexOutboxEvent, get_session
from shared.logging_config import logger


class IndexOutboxService:
    """Сервис outbox-событий для синхронизации индекса."""

    @staticmethod
    def _canonical_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not payload:
            return {}
        try:
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
            return json.loads(raw)
        except Exception:
            return {}

    @staticmethod
    def build_idempotency_key(
        *,
        operation: str,
        knowledge_base_id: int,
        document_id: Optional[int],
        version: Optional[int],
        payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        payload_obj = IndexOutboxService._canonical_payload(payload)
        payload_raw = json.dumps(payload_obj, ensure_ascii=False, sort_keys=True)
        payload_hash = hashlib.sha256(payload_raw.encode("utf-8", errors="ignore")).hexdigest()[:16]
        return f"{operation}:{knowledge_base_id}:{document_id or 0}:{version or 0}:{payload_hash}"

    def enqueue_event(
        self,
        *,
        operation: str,
        knowledge_base_id: int,
        document_id: Optional[int] = None,
        version: Optional[int] = None,
        payload: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> IndexOutboxEvent:
        payload_obj = self._canonical_payload(payload)
        key = idempotency_key or self.build_idempotency_key(
            operation=operation,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            version=version,
            payload=payload_obj,
        )

        with get_session() as session:
            existing = session.query(IndexOutboxEvent).filter_by(idempotency_key=key).first()
            if existing:
                return existing

            now = datetime.now(timezone.utc)
            event = IndexOutboxEvent(
                event_id=uuid4().hex,
                idempotency_key=key,
                knowledge_base_id=int(knowledge_base_id),
                document_id=int(document_id) if document_id is not None else None,
                version=int(version) if version is not None else None,
                operation=(operation or "UPSERT").upper(),
                payload_json=json.dumps(payload_obj, ensure_ascii=False) if payload_obj else None,
                status="pending",
                attempt_count=0,
                available_at=now,
                created_at=now,
            )
            session.add(event)
            session.flush()
            return event

    def claim_pending(self, *, limit: int = 100) -> List[IndexOutboxEvent]:
        now = datetime.now(timezone.utc)
        claim_limit = max(1, int(limit))
        with get_session() as session:
            candidates = (
                session.query(IndexOutboxEvent)
                .filter_by(status="pending")
                .order_by(IndexOutboxEvent.created_at.asc())
                .limit(claim_limit * 4)
                .all()
            )
            ready = [row for row in candidates if not row.available_at or row.available_at <= now][:claim_limit]
            for row in ready:
                row.status = "processing"
                row.attempt_count = int(row.attempt_count or 0) + 1
                row.locked_at = now
            session.flush()
            return ready

    def mark_processed(self, *, event_id: str) -> bool:
        with get_session() as session:
            row = session.query(IndexOutboxEvent).filter_by(event_id=event_id).first()
            if not row:
                return False
            row.status = "processed"
            row.processed_at = datetime.now(timezone.utc)
            row.locked_at = None
            row.last_error = None
            session.flush()
            return True

    def mark_failed(self, *, event_id: str, error: str, retry_delay_sec: int = 30) -> bool:
        delay = max(0, int(retry_delay_sec))
        with get_session() as session:
            row = session.query(IndexOutboxEvent).filter_by(event_id=event_id).first()
            if not row:
                return False
            row.status = "pending"
            row.available_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            row.locked_at = None
            row.last_error = (error or "")[:2000]
            session.flush()
            return True

    def mark_dead(self, *, event_id: str, error: str) -> bool:
        with get_session() as session:
            row = session.query(IndexOutboxEvent).filter_by(event_id=event_id).first()
            if not row:
                return False
            row.status = "dead"
            row.locked_at = None
            row.last_error = (error or "")[:2000]
            session.flush()
            return True

    def pending_count(self) -> int:
        with get_session() as session:
            return int(session.query(IndexOutboxEvent).filter_by(status="pending").count() or 0)


index_outbox_service = IndexOutboxService()
