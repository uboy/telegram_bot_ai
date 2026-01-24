from typing import Optional, List, Dict

from pydantic import BaseModel


class RAGQuery(BaseModel):
    telegram_id: Optional[str] = None
    query: str
    knowledge_base_id: Optional[int] = None
    top_k: Optional[int] = None
    source_types: Optional[List[str]] = None
    languages: Optional[List[str]] = None
    path_prefixes: Optional[List[str]] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None


class RAGSource(BaseModel):
    source_path: str
    source_type: str
    score: float


class RAGAnswer(BaseModel):
    answer: str
    sources: List[RAGSource] = []
    debug_chunks: Optional[List[Dict]] = None  # Для debug mode: первые N чанков с метаданными


class RAGSummaryQuery(BaseModel):
    query: str
    knowledge_base_id: Optional[int] = None
    mode: Optional[str] = "summary"  # summary | faq | instructions
    top_k: Optional[int] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None


class RAGSummaryAnswer(BaseModel):
    answer: str
    sources: List[RAGSource] = []


