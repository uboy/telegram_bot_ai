import pytest

pytest.importorskip("fastapi")

from backend.api.routes.jobs import get_job_status
from shared.database import Job


class DummySession:
    def __init__(self, job):
        self._job = job

    def query(self, _):
        return self

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return self._job


def test_get_job_status_found():
    job = Job()
    job.id = 1
    job.status = "processing"
    job.progress = 50
    job.stage = "ingestion"
    job.error_message = None

    resp = get_job_status(1, db=DummySession(job))
    assert resp.job_id == 1
    assert resp.status == "processing"
