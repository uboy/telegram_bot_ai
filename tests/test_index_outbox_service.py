from contextlib import contextmanager
from datetime import datetime, timezone

from shared.database import IndexOutboxEvent
from shared.index_outbox_service import IndexOutboxService


class DummyQuery:
    def __init__(self, rows):
        self._rows = rows
        self._limit = None

    def filter_by(self, **kwargs):
        def _match(row):
            for key, value in kwargs.items():
                if getattr(row, key, None) != value:
                    return False
            return True

        self._rows = [row for row in self._rows if _match(row)]
        return self

    def order_by(self, *_args, **_kwargs):
        self._rows = sorted(self._rows, key=lambda row: getattr(row, "created_at", datetime.min))
        return self

    def limit(self, value):
        self._limit = int(value)
        return self

    def all(self):
        rows = list(self._rows)
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    def first(self):
        rows = self.all()
        return rows[0] if rows else None

    def count(self):
        return len(self.all())


class DummySession:
    def __init__(self, storage):
        self.storage = storage

    def query(self, model):
        model_name = getattr(model, "__name__", "")
        if model_name == "IndexOutboxEvent":
            return DummyQuery(self.storage)
        return DummyQuery([])

    def add(self, obj):
        self.storage.append(obj)

    def flush(self):
        return None


@contextmanager
def _dummy_session_ctx(storage):
    yield DummySession(storage)


def test_enqueue_event_idempotent(monkeypatch):
    storage = []
    monkeypatch.setattr("shared.index_outbox_service.get_session", lambda: _dummy_session_ctx(storage))

    service = IndexOutboxService()
    payload_a = {"source_path": "doc.md", "chunks": 15}
    payload_b = {"chunks": 15, "source_path": "doc.md"}  # same semantic payload, different key order

    event1 = service.enqueue_event(
        operation="UPSERT",
        knowledge_base_id=1,
        document_id=10,
        version=2,
        payload=payload_a,
    )
    event2 = service.enqueue_event(
        operation="UPSERT",
        knowledge_base_id=1,
        document_id=10,
        version=2,
        payload=payload_b,
    )

    assert len(storage) == 1
    assert event1.event_id == event2.event_id
    assert storage[0].status == "pending"


def test_claim_and_status_lifecycle(monkeypatch):
    storage = []
    monkeypatch.setattr("shared.index_outbox_service.get_session", lambda: _dummy_session_ctx(storage))

    service = IndexOutboxService()
    event1 = service.enqueue_event(
        operation="UPSERT",
        knowledge_base_id=7,
        document_id=701,
        version=1,
        payload={"name": "doc-a"},
    )
    event2 = service.enqueue_event(
        operation="UPSERT",
        knowledge_base_id=7,
        document_id=702,
        version=1,
        payload={"name": "doc-b"},
    )

    claimed = service.claim_pending(limit=1)
    assert len(claimed) == 1
    assert claimed[0].status == "processing"
    assert claimed[0].attempt_count == 1
    assert claimed[0].locked_at is not None

    assert service.mark_failed(event_id=event1.event_id, error="temporary", retry_delay_sec=0) is True
    row1 = next(r for r in storage if r.event_id == event1.event_id)
    assert row1.status == "pending"
    assert row1.last_error == "temporary"

    assert service.mark_processed(event_id=event1.event_id) is True
    assert row1.status == "processed"
    assert row1.processed_at is not None

    assert service.mark_dead(event_id=event2.event_id, error="permanent") is True
    row2 = next(r for r in storage if r.event_id == event2.event_id)
    assert row2.status == "dead"
    assert row2.last_error == "permanent"


def test_pending_count(monkeypatch):
    storage = []
    monkeypatch.setattr("shared.index_outbox_service.get_session", lambda: _dummy_session_ctx(storage))

    service = IndexOutboxService()
    service.enqueue_event(
        operation="UPSERT",
        knowledge_base_id=3,
        document_id=30,
        version=1,
        payload={"kind": "a"},
    )
    evt = service.enqueue_event(
        operation="UPSERT",
        knowledge_base_id=3,
        document_id=31,
        version=1,
        payload={"kind": "b"},
    )
    row = next(r for r in storage if r.event_id == evt.event_id)
    row.status = "processed"
    row.processed_at = datetime.now(timezone.utc)

    assert service.pending_count() == 1

