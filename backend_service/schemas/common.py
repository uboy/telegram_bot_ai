from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class BaseResponse(BaseModel):
    status: str = "ok"


class KnowledgeBaseInfo(BaseModel):
    id: int
    name: str
    description: Optional[str] = None


class KnowledgeSourceInfo(BaseModel):
    source_path: str
    source_type: str
    chunks_count: int
    last_updated: Optional[datetime] = None
