from typing import Optional, List

from pydantic import BaseModel


class RAGQuery(BaseModel):
    telegram_id: Optional[str] = None
    query: str
    knowledge_base_id: Optional[int] = None
    top_k: Optional[int] = None


class RAGSource(BaseModel):
    source_path: str
    source_type: str
    score: float


class RAGAnswer(BaseModel):
    answer: str
    sources: List[RAGSource] = []


