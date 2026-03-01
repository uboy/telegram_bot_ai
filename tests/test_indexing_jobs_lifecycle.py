import tempfile
from pathlib import Path

import pytest

from backend.services.indexing_service import IndexingService


def test_document_job_lifecycle_success(monkeypatch):
    calls = []

    class DummyIngestionService:
        def __init__(self, _session):
            pass

        def ingest_document_or_archive(self, **payload):
            calls.append(("ingest", payload.get("kb_id")))

    class DummySession:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    statuses = []
    service = IndexingService()
    monkeypatch.setattr("backend.services.indexing_service.IngestionService", DummyIngestionService)
    monkeypatch.setattr("backend.services.indexing_service.get_session", lambda: DummySession())
    monkeypatch.setattr(
        service,
        "update_job",
        lambda job_id, status, progress, stage=None, error=None: statuses.append((job_id, status, progress, stage, error)),
    )

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name

    payload = {
        "kb_id": 1,
        "file_path": tmp_path,
        "file_name": "doc.md",
        "file_type": "markdown",
    }
    service.run_document_job(42, payload)

    assert calls == [("ingest", 1)]
    assert statuses[0][1:] == ("processing", 5, "ingestion", None)
    assert statuses[-1][1:] == ("completed", 100, "done", None)
    assert not Path(tmp_path).exists()


def test_document_job_lifecycle_failure(monkeypatch):
    class DummyIngestionService:
        def __init__(self, _session):
            pass

        def ingest_document_or_archive(self, **payload):
            raise RuntimeError("boom")

    class DummySession:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    statuses = []
    service = IndexingService()
    monkeypatch.setattr("backend.services.indexing_service.IngestionService", DummyIngestionService)
    monkeypatch.setattr("backend.services.indexing_service.get_session", lambda: DummySession())
    monkeypatch.setattr(
        service,
        "update_job",
        lambda job_id, status, progress, stage=None, error=None: statuses.append((job_id, status, progress, stage, error)),
    )

    payload = {"kb_id": 1, "file_path": "", "file_name": "doc.md", "file_type": "markdown"}
    service.run_document_job(77, payload)

    assert statuses[0][1:] == ("processing", 5, "ingestion", None)
    assert statuses[-1][1] == "failed"
    assert statuses[-1][3] == "error"
    assert "boom" in (statuses[-1][4] or "")
