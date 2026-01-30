from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from queue import Queue, Full
from threading import Lock
from typing import Dict, Optional
from uuid import uuid4

from shared.logging_config import logger  # type: ignore

@dataclass
class AsrJob:
    job_id: str
    file_path: str
    telegram_id: int
    message_id: int
    language: Optional[str]
    created_at: datetime


@dataclass
class AsrJobStatus:
    job_id: str
    status: str
    text: Optional[str] = None
    error: Optional[str] = None
    queue_position: Optional[int] = None


_queue_max = int(os.getenv("ASR_QUEUE_MAX", "100"))
_job_queue: "Queue[AsrJob]" = Queue(maxsize=_queue_max)
_job_statuses: Dict[str, AsrJobStatus] = {}
_status_lock = Lock()


def enqueue_asr_job(
    file_path: str,
    telegram_id: int,
    message_id: int,
    language: Optional[str] = None,
) -> str:
    job_id = str(uuid4())
    job = AsrJob(
        job_id=job_id,
        file_path=file_path,
        telegram_id=telegram_id,
        message_id=message_id,
        language=language,
        created_at=datetime.now(timezone.utc),
    )
    try:
        _job_queue.put_nowait(job)
    except Full as exc:
        raise RuntimeError("queue is full") from exc

    with _status_lock:
        _job_statuses[job_id] = AsrJobStatus(
            job_id=job_id,
            status="queued",
            queue_position=_job_queue.qsize(),
        )
    logger.info("ASR queued job=%s queue_size=%s", job_id, _job_queue.qsize())
    return job_id


def get_job_status(job_id: str) -> Optional[AsrJobStatus]:
    with _status_lock:
        return _job_statuses.get(job_id)


def set_job_status(
    job_id: str,
    status: str,
    text: Optional[str] = None,
    error: Optional[str] = None,
    queue_position: Optional[int] = None,
) -> None:
    with _status_lock:
        current = _job_statuses.get(job_id)
        if not current:
            _job_statuses[job_id] = AsrJobStatus(
                job_id=job_id,
                status=status,
                text=text,
                error=error,
                queue_position=queue_position,
            )
            return
        current.status = status
        current.text = text if text is not None else current.text
        current.error = error if error is not None else current.error
        current.queue_position = queue_position


def next_job() -> AsrJob:
    return _job_queue.get()


def mark_done(job: AsrJob) -> None:
    _job_queue.task_done()


def reset_queue(maxsize: Optional[int] = None) -> None:
    global _job_queue
    with _status_lock:
        _job_statuses.clear()
    _job_queue = Queue(maxsize=maxsize or _queue_max)
