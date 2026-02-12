"""Pydantic schemas for chat analytics API."""
from typing import Optional, List
from datetime import datetime

from pydantic import BaseModel


# ── Request schemas ────────────────────────────────────────────────────

class MessagePayload(BaseModel):
    chat_id: str
    thread_id: Optional[int] = None
    message_id: int
    author_telegram_id: Optional[str] = None
    author_username: Optional[str] = None
    author_display_name: Optional[str] = None
    text: str
    timestamp: str
    message_link: Optional[str] = None
    is_bot_message: bool = False
    is_system_message: bool = False


class MessageBatchPayload(BaseModel):
    messages: List[MessagePayload]


class AnalyticsConfigUpdate(BaseModel):
    chat_title: Optional[str] = None
    collection_enabled: Optional[bool] = None
    analysis_enabled: Optional[bool] = None
    digest_cron: Optional[str] = None
    digest_period_hours: Optional[int] = None
    digest_timezone: Optional[str] = None
    delivery_chat_id: Optional[str] = None
    delivery_thread_id: Optional[int] = None
    delivery_to_admins: Optional[bool] = None


class DigestGenerateRequest(BaseModel):
    chat_id: str
    period_start: str
    period_end: str


class SearchRequest(BaseModel):
    query: str
    chat_id: str
    thread_id: Optional[int] = None
    author_telegram_id: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    top_k: int = 10


class QARequest(BaseModel):
    question: str
    chat_id: str
    thread_id: Optional[int] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None


# ── Response schemas ───────────────────────────────────────────────────

class AnalyticsConfigResponse(BaseModel):
    chat_id: str
    chat_title: Optional[str] = None
    collection_enabled: bool = True
    analysis_enabled: bool = True
    digest_cron: Optional[str] = None
    digest_period_hours: int = 168
    digest_timezone: str = 'UTC'
    delivery_chat_id: Optional[str] = None
    delivery_thread_id: Optional[int] = None
    delivery_to_admins: bool = False
    configured_by: Optional[str] = None

    class Config:
        from_attributes = True


class ChatDigestThemeResponse(BaseModel):
    id: int
    emoji: Optional[str] = None
    title: str
    summary: str
    related_thread_ids: Optional[str] = None
    key_message_links: Optional[str] = None
    main_participants: Optional[str] = None
    message_count: int = 0
    sort_order: int = 0

    class Config:
        from_attributes = True


class ChatDigestResponse(BaseModel):
    id: int
    chat_id: str
    period_start: datetime
    period_end: datetime
    summary_text: Optional[str] = None
    theme_count: int = 0
    total_messages_analyzed: int = 0
    generation_time_sec: Optional[int] = None
    llm_model_used: Optional[str] = None
    status: str = 'pending'
    error_message: Optional[str] = None
    delivered: bool = False
    delivered_at: Optional[datetime] = None
    themes: List[ChatDigestThemeResponse] = []

    class Config:
        from_attributes = True


class SearchResultItem(BaseModel):
    message_id: int
    text: str
    author: Optional[str] = None
    timestamp: str
    message_link: Optional[str] = None
    thread_id: Optional[int] = None
    context_before: Optional[str] = None
    context_after: Optional[str] = None
    score: float


class SearchResultResponse(BaseModel):
    results: List[SearchResultItem] = []
    total_found: int = 0


class QAResponse(BaseModel):
    answer: str
    sources: List[SearchResultItem] = []
    confidence: str = "low"


class ChatStatsResponse(BaseModel):
    total_messages: int = 0
    unique_authors: int = 0
    active_threads: int = 0
    messages_per_day: float = 0.0
    top_authors: List[dict] = []
    period: dict = {}


class ImportStatusResponse(BaseModel):
    import_id: int
    status: str = 'pending'
    messages_imported: int = 0
    messages_skipped: int = 0
    error_message: Optional[str] = None

    class Config:
        from_attributes = True
