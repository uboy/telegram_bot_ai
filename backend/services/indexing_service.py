from dataclasses import dataclass
from typing import Any, Dict, Optional
import threading

from shared.database import Job, get_session
from backend.services.ingestion_service import IngestionService


@dataclass
class JobResult:
    job_id: int
    status: str
    progress: int
    stage: Optional[str] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


class IndexingService:
    def __init__(self, db: Optional[object] = None) -> None:
        # db is optional; background jobs always open their own session
        self.db = db

    def create_job(self, stage: str = "pending") -> Job:
        with get_session() as session:
            job = Job(status="pending", progress=0, stage=stage)
            session.add(job)
            session.flush()
            session.refresh(job)
            return job

    def update_job(self, job_id: int, status: str, progress: int, stage: Optional[str] = None, error: Optional[str] = None) -> None:
        with get_session() as session:
            job = session.query(Job).filter_by(id=job_id).first()
            if not job:
                return
            job.status = status
            job.progress = progress
            if stage:
                job.stage = stage
            if error:
                job.error_message = error

    def get_job(self, job_id: int) -> Optional[Job]:
        with get_session() as session:
            return session.query(Job).filter_by(id=job_id).first()

    def run_document_job(self, job_id: int, payload: Dict[str, Any]) -> None:
        try:
            self.update_job(job_id, status="processing", progress=5, stage="ingestion")
            with get_session() as session:
                service = IngestionService(session)
                service.ingest_document_or_archive(**payload)
            self.update_job(job_id, status="completed", progress=100, stage="done")
        except Exception as e:  # noqa: BLE001
            self.update_job(job_id, status="failed", progress=100, stage="error", error=str(e))
        finally:
            if payload.get("file_path"):
                try:
                    import os
                    os.unlink(payload["file_path"])
                except OSError:
                    pass

    def run_web_job(self, job_id: int, payload: Dict[str, Any]) -> None:
        try:
            self.update_job(job_id, status="processing", progress=5, stage="ingestion")
            with get_session() as session:
                service = IngestionService(session)
                service.ingest_web_page(**payload)
            self.update_job(job_id, status="completed", progress=100, stage="done")
        except Exception as e:  # noqa: BLE001
            self.update_job(job_id, status="failed", progress=100, stage="error", error=str(e))

    def run_code_job(self, job_id: int, payload: Dict[str, Any], mode: str) -> None:
        try:
            self.update_job(job_id, status="processing", progress=5, stage="ingestion")
            with get_session() as session:
                service = IngestionService(session)
                if mode == "path":
                    service.ingest_codebase_path(**payload)
                else:
                    service.ingest_codebase_git(**payload)
            self.update_job(job_id, status="completed", progress=100, stage="done")
        except Exception as e:  # noqa: BLE001
            self.update_job(job_id, status="failed", progress=100, stage="error", error=str(e))

    def run_image_job(self, job_id: int, payload: Dict[str, Any]) -> None:
        try:
            self.update_job(job_id, status="processing", progress=5, stage="ingestion")
            with get_session() as session:
                service = IngestionService(session)
                service.ingest_image(**payload)
            self.update_job(job_id, status="completed", progress=100, stage="done")
        except Exception as e:  # noqa: BLE001
            self.update_job(job_id, status="failed", progress=100, stage="error", error=str(e))
        finally:
            if payload.get("file_path"):
                try:
                    import os
                    os.unlink(payload["file_path"])
                except OSError:
                    pass

    def run_async(self, target, job_id: int, payload: Dict[str, Any]) -> None:
        thread = threading.Thread(target=target, args=(job_id, payload), daemon=True)
        thread.start()
