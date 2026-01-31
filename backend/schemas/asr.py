from typing import Optional

from pydantic import BaseModel, Field


class AsrJobQueued(BaseModel):
    job_id: str
    status: str = "queued"
    queue_position: int = Field(..., ge=1)


class AsrJobStatus(BaseModel):
    job_id: str
    status: str
    text: Optional[str] = None
    error: Optional[str] = None
    audio_meta: Optional[dict] = None
    timing_meta: Optional[dict] = None


class AsrSettings(BaseModel):
    asr_provider: str
    asr_model_name: str
    asr_device: Optional[str] = None


class AsrSettingsUpdate(BaseModel):
    asr_provider: Optional[str] = None
    asr_model_name: Optional[str] = None
    asr_device: Optional[str] = None
