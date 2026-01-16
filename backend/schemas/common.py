from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Any, Dict


class BaseResponse(BaseModel):
    status: str = "ok"


class KnowledgeBaseInfo(BaseModel):
    id: int
    name: str
    description: Optional[str] = None


class KnowledgeBaseCreate(BaseModel):
    name: str
    description: Optional[str] = None


class KnowledgeBaseSettings(BaseModel):
    settings: Dict[str, Any]


class KnowledgeSourceInfo(BaseModel):
    source_path: str
    source_type: str
    chunks_count: int
    last_updated: Optional[datetime] = None


class KnowledgeImportLogEntry(BaseModel):
    created_at: datetime
    username: Optional[str] = None
    user_telegram_id: Optional[str] = None
    action_type: str
    source_path: str
    total_chunks: int
