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


def test_ingest_wiki_crawl_returns_sync_mode_fields(monkeypatch):
    class DummyIngestionService:
        def __init__(self, _db):
            return None

        def ingest_wiki_crawl(self, *, kb_id, wiki_url, telegram_id=None, username=None):
            return {
                "deleted_chunks": 2,
                "pages_processed": 9,
                "chunks_added": 33,
                "wiki_root": wiki_url,
                "crawl_mode": "html",
                "git_fallback_attempted": True,
            }

    monkeypatch.setattr(ingestion_routes, "IngestionService", DummyIngestionService)

    result = ingestion_routes.ingest_wiki_crawl(
        knowledge_base_id=42,
        url="https://gitee.com/mazurdenis/open-harmony/wikis",
        telegram_id="1",
        username="admin",
        db=object(),
    )

    assert result.deleted_chunks == 2
    assert result.pages_processed == 9
    assert result.chunks_added == 33
    assert result.wiki_root == "https://gitee.com/mazurdenis/open-harmony/wikis"
    assert result.crawl_mode == "html"
    assert result.git_fallback_attempted is True
