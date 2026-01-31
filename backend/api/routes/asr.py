from __future__ import annotations

import os
import tempfile
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from backend.api.deps import require_api_key
from backend.schemas.asr import AsrJobQueued, AsrJobStatus, AsrSettings, AsrSettingsUpdate
from backend.services.asr_queue import enqueue_asr_job, get_job_status
from shared.database import Session, AppSettings  # type: ignore
from shared.logging_config import logger
from shared.asr_limits import get_asr_max_file_bytes


router = APIRouter(prefix="/asr", tags=["asr"])


@router.post(
    "/transcribe",
    response_model=AsrJobQueued,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_api_key)],
)
async def transcribe_voice(
    file: UploadFile = File(...),
    telegram_id: str = Form(...),
    message_id: str = Form(...),
    language: Optional[str] = Form(None),
    message_date: Optional[str] = Form(None),
) -> AsrJobQueued:
    if not file:
        raise HTTPException(status_code=400, detail="audio file is required")

    content_type = (file.content_type or "").lower()
    suffix = Path(file.filename or "").suffix.lower()
    logger.info(
        "ASR upload: filename=%s content_type=%s suffix=%s",
        file.filename,
        content_type or None,
        suffix or None,
    )
    allowed_types = {
        "audio/ogg",
        "audio/opus",
        "audio/mpeg",
        "audio/wav",
        "audio/x-wav",
        "audio/mp4",
        "audio/webm",
        "application/octet-stream",
    }
    if content_type and content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"unsupported audio type: {content_type}")

    if suffix != ".wav" and not shutil.which("ffmpeg"):
        raise HTTPException(
            status_code=503,
            detail="ffmpeg not found in PATH. Install ffmpeg and restart the backend "
                   "or upload WAV audio.",
        )

    max_bytes = get_asr_max_file_bytes()
    suffix = suffix or ".ogg"

    total = 0
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
        temp_path = tmp_file.name
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                tmp_file.close()
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
                raise HTTPException(
                    status_code=413,
                    detail={
                        "message": "audio file is too large",
                        "limit_bytes": max_bytes,
                        "size_bytes": total,
                    },
                )
            tmp_file.write(chunk)

    audio_meta = {
        "original_name": file.filename or "",
        "size_bytes": total,
        "sent_at": message_date,
    }
    try:
        job_id = enqueue_asr_job(
            file_path=temp_path,
            telegram_id=int(telegram_id),
            message_id=int(message_id),
            language=language,
            audio_meta=audio_meta,
        )
    except RuntimeError as exc:
        try:
            os.remove(temp_path)
        except Exception:
            pass
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        try:
            os.remove(temp_path)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="telegram_id/message_id must be integers") from exc
    except Exception as exc:  # noqa: BLE001
        try:
            os.remove(temp_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"failed to enqueue ASR job: {exc}") from exc

    status_obj = get_job_status(job_id)
    queue_position = status_obj.queue_position if status_obj and status_obj.queue_position else 1
    return AsrJobQueued(job_id=job_id, queue_position=queue_position)


@router.get(
    "/jobs/{job_id}",
    response_model=AsrJobStatus,
    dependencies=[Depends(require_api_key)],
)
def get_asr_job(job_id: str) -> AsrJobStatus:
    status_obj = get_job_status(job_id)
    if not status_obj:
        raise HTTPException(status_code=404, detail="job not found")
    return AsrJobStatus(
        job_id=status_obj.job_id,
        status=status_obj.status,
        text=status_obj.text,
        error=status_obj.error,
        audio_meta=status_obj.audio_meta,
        timing_meta=status_obj.timing_meta,
    )


@router.get(
    "/settings",
    response_model=AsrSettings,
    dependencies=[Depends(require_api_key)],
)
def get_asr_settings() -> AsrSettings:
    session = Session()
    try:
        settings = session.query(AppSettings).first()
        if not settings:
            settings = AppSettings()
            session.add(settings)
            session.commit()
            session.refresh(settings)
        return AsrSettings(
            asr_provider=settings.asr_provider or "transformers",
            asr_model_name=settings.asr_model_name or "openai/whisper-small",
            asr_device=settings.asr_device or None,
        )
    finally:
        session.close()


@router.put(
    "/settings",
    response_model=AsrSettings,
    dependencies=[Depends(require_api_key)],
)
def update_asr_settings(payload: AsrSettingsUpdate) -> AsrSettings:
    session = Session()
    try:
        settings = session.query(AppSettings).first()
        if not settings:
            settings = AppSettings()
            session.add(settings)

        if payload.asr_provider is not None:
            settings.asr_provider = payload.asr_provider.strip() or "transformers"
        if payload.asr_model_name is not None:
            settings.asr_model_name = payload.asr_model_name.strip()
        if payload.asr_device is not None:
            settings.asr_device = payload.asr_device.strip() or ""

        session.commit()
        session.refresh(settings)
        return AsrSettings(
            asr_provider=settings.asr_provider or "transformers",
            asr_model_name=settings.asr_model_name or "openai/whisper-small",
            asr_device=settings.asr_device or None,
        )
    finally:
        session.close()
