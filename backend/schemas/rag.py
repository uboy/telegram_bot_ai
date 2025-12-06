from typing import Optional, List, Dict

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
    debug_chunks: Optional[List[Dict]] = None  # Для debug mode: первые N чанков с метаданными


