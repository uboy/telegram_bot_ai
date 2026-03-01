import pytest

pytest.importorskip("fastapi")

from backend.api.routes import ingestion as ingestion_routes


class DummyJob:
    def __init__(self, job_id):
        self.id = job_id


class DummyIndexingService:
    def __init__(self, _db):
        self.created = []

    def create_job(self, stage="pending"):
        self.created.append(stage)
        return DummyJob(101)

    def run_async(self, *_args, **_kwargs):
        return None

    def run_web_job(self, *_args, **_kwargs):
        return None


def test_ingest_web_page_returns_job_id(monkeypatch):
    monkeypatch.setattr(ingestion_routes, "IndexingService", DummyIndexingService)
    payload = ingestion_routes.WebIngestionRequest(
        knowledge_base_id=2,
        url="https://example.com/wiki",
        telegram_id="1",
        username="user",
    )

    result = ingestion_routes.ingest_web_page_endpoint(payload=payload, db=object())
    assert result.job_id == 101
    assert result.kb_id == 2


def test_ingest_codebase_path_returns_job_id(monkeypatch):
    monkeypatch.setattr(ingestion_routes, "IndexingService", DummyIndexingService)
    payload = ingestion_routes.CodePathIngestionRequest(
        knowledge_base_id=3,
        path="/repo/path",
        repo_label="repo",
    )

    result = ingestion_routes.ingest_codebase_path(payload=payload, db=object())
    assert result.job_id == 101
    assert result.root == "/repo/path"
