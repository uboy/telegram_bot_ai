import json
from types import SimpleNamespace

from backend.services import index_outbox_worker as worker


def _event(*, event_id: str, operation: str = "UPSERT", attempt_count: int = 1, payload: dict | None = None):
    return SimpleNamespace(
        event_id=event_id,
        operation=operation,
        attempt_count=attempt_count,
        knowledge_base_id=1,
        document_id=10,
        version=2,
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
    )


def test_process_pending_events_marks_processed(monkeypatch):
    event = _event(event_id="evt-1", payload={"source_type": "pdf", "source_path": "doc://a"})
    captured = {"processed": [], "failed": [], "dead": []}

    monkeypatch.setattr(worker.index_outbox_service, "claim_pending", lambda limit=100: [event])
    monkeypatch.setattr(
        worker.index_outbox_service,
        "mark_processed",
        lambda **kwargs: captured["processed"].append(kwargs.get("event_id")) or True,
    )
    monkeypatch.setattr(
        worker.index_outbox_service,
        "mark_failed",
        lambda **kwargs: captured["failed"].append(kwargs) or True,
    )
    monkeypatch.setattr(
        worker.index_outbox_service,
        "mark_dead",
        lambda **kwargs: captured["dead"].append(kwargs) or True,
    )
    monkeypatch.setattr(worker, "_process_upsert_event", lambda evt, payload: 1)

    handled = worker.process_pending_events_once(limit=1)

    assert handled == 1
    assert captured["processed"] == ["evt-1"]
    assert captured["failed"] == []
    assert captured["dead"] == []


def test_process_pending_events_schedules_retry(monkeypatch):
    event = _event(event_id="evt-2", attempt_count=1, payload={"source_type": "pdf"})
    captured = {"processed": [], "failed": [], "dead": []}

    monkeypatch.setenv("RAG_INDEX_OUTBOX_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("RAG_INDEX_OUTBOX_RETRY_BASE_SEC", "5")
    monkeypatch.setenv("RAG_INDEX_OUTBOX_RETRY_MAX_SEC", "300")

    monkeypatch.setattr(worker.index_outbox_service, "claim_pending", lambda limit=100: [event])
    monkeypatch.setattr(
        worker.index_outbox_service,
        "mark_processed",
        lambda **kwargs: captured["processed"].append(kwargs.get("event_id")) or True,
    )
    monkeypatch.setattr(
        worker.index_outbox_service,
        "mark_failed",
        lambda **kwargs: captured["failed"].append(kwargs) or True,
    )
    monkeypatch.setattr(
        worker.index_outbox_service,
        "mark_dead",
        lambda **kwargs: captured["dead"].append(kwargs) or True,
    )

    def _boom(evt, payload):  # noqa: ARG001
        raise RuntimeError("temporary")

    monkeypatch.setattr(worker, "_process_upsert_event", _boom)

    handled = worker.process_pending_events_once(limit=1)

    assert handled == 0
    assert captured["processed"] == []
    assert len(captured["failed"]) == 1
    assert captured["failed"][0]["event_id"] == "evt-2"
    assert captured["failed"][0]["retry_delay_sec"] == 5
    assert captured["dead"] == []


def test_process_pending_events_marks_dead_after_max_attempts(monkeypatch):
    event = _event(event_id="evt-3", attempt_count=3, payload={"source_type": "pdf"})
    captured = {"processed": [], "failed": [], "dead": []}

    monkeypatch.setenv("RAG_INDEX_OUTBOX_MAX_ATTEMPTS", "3")
    monkeypatch.setattr(worker.index_outbox_service, "claim_pending", lambda limit=100: [event])
    monkeypatch.setattr(
        worker.index_outbox_service,
        "mark_processed",
        lambda **kwargs: captured["processed"].append(kwargs.get("event_id")) or True,
    )
    monkeypatch.setattr(
        worker.index_outbox_service,
        "mark_failed",
        lambda **kwargs: captured["failed"].append(kwargs) or True,
    )
    monkeypatch.setattr(
        worker.index_outbox_service,
        "mark_dead",
        lambda **kwargs: captured["dead"].append(kwargs) or True,
    )

    def _boom(evt, payload):  # noqa: ARG001
        raise RuntimeError("permanent")

    monkeypatch.setattr(worker, "_process_upsert_event", _boom)

    handled = worker.process_pending_events_once(limit=1)

    assert handled == 0
    assert captured["processed"] == []
    assert captured["failed"] == []
    assert len(captured["dead"]) == 1
    assert captured["dead"][0]["event_id"] == "evt-3"


def test_run_index_drift_audit_once_records_statuses(monkeypatch):
    class DummyBackend:
        def __init__(self, counts):
            self._counts = counts

        def count_points(self, kb_id: int):
            return self._counts.get(int(kb_id), 0)

    audits = []
    monkeypatch.setenv("RAG_INDEX_DRIFT_WARNING_RATIO", "0.05")
    monkeypatch.setenv("RAG_INDEX_DRIFT_CRITICAL_RATIO", "0.2")
    monkeypatch.setattr(worker, "_get_qdrant_backend", lambda: DummyBackend({1: 100, 2: 90, 3: 70}))
    monkeypatch.setattr(worker, "_list_active_kb_ids_with_embeddings", lambda max_kbs=None: [1, 2, 3])  # noqa: ARG005
    monkeypatch.setattr(worker, "_count_expected_chunks", lambda kb_id: 100)  # noqa: ARG005
    monkeypatch.setattr(worker, "_record_sync_audit", lambda **kwargs: audits.append(kwargs))

    written = worker.run_index_drift_audit_once(max_kbs=3)

    assert written == 3
    status_by_kb = {row["kb_id"]: row["status"] for row in audits}
    assert status_by_kb[1] == "ok"
    assert status_by_kb[2] == "warning"
    assert status_by_kb[3] == "critical"


def test_worker_config_exposes_retention_knobs(monkeypatch):
    monkeypatch.setenv("RAG_RETENTION_ENABLED", "true")
    monkeypatch.setenv("RAG_RETENTION_INTERVAL_SEC", "7200")

    cfg = worker._worker_config()

    assert cfg["retention_enabled"] is True
    assert cfg["retention_interval_sec"] == 7200.0
