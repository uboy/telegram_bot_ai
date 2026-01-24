from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.deps import get_db_dep, require_api_key
from shared.database import Job


class JobStatusResponse(BaseModel):
    job_id: int
    status: str
    progress: int
    stage: str | None = None
    error: str | None = None


router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="Получить статус асинхронной задачи",
    dependencies=[Depends(require_api_key)],
)
def get_job_status(job_id: int, db: Session = Depends(get_db_dep)) -> JobStatusResponse:
    job = db.query(Job).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        progress=job.progress,
        stage=job.stage,
        error=job.error_message,
    )
