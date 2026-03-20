from typing import Optional, List, Dict, Literal

from pydantic import BaseModel


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str


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
    # Контекст диалога для переформулировки (RAGCONV-001)
    conversation_context: Optional[List[ConversationTurn]] = None


class RAGSource(BaseModel):
    source_path: str
    source_type: str
    score: float
    page_number: Optional[int] = None
    section_title: Optional[str] = None


class RAGAnswer(BaseModel):
    answer: str
    sources: List[RAGSource] = []
    request_id: Optional[str] = None
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


class RAGDiagnosticsCandidate(BaseModel):
    rank: int
    source_path: str
    source_type: str
    distance: Optional[str] = None
    rerank_score: Optional[str] = None
    origin: str
    channel: str
    channel_rank: int
    fusion_rank: int
    fusion_score: str
    rerank_delta: Optional[str] = None
    included_in_context: bool
    context_rank: Optional[int] = None
    context_reason: Optional[str] = None
    context_anchor_rank: Optional[int] = None
    family_key: Optional[str] = None
    family_rank: Optional[int] = None
    canonicality_score: Optional[str] = None
    contamination_penalty: Optional[str] = None
    canonicality_reason: Optional[str] = None
    contamination_reason: Optional[str] = None
    metadata: Optional[Dict] = None
    content_preview: Optional[str] = None


class RAGDiagnosticsResponse(BaseModel):
    request_id: str
    query: str
    knowledge_base_id: Optional[int] = None
    intent: Optional[str] = None
    orchestrator_mode: Optional[str] = None
    retrieval_core_mode: Optional[str] = None
    backend_name: Optional[str] = None
    total_candidates: int = 0
    total_selected: int = 0
    latency_ms: int = 0
    degraded_mode: bool = False
    degraded_reason: Optional[str] = None
    hints: Optional[Dict] = None
    filters: Optional[Dict] = None
    candidates: List[RAGDiagnosticsCandidate] = []


class RAGEvalRunRequest(BaseModel):
    suite: str = "rag-general-v1"
    baseline_run_id: Optional[str] = None
    slices: Optional[List[str]] = None
    run_with_judge: bool = False  # Флаг запуска LLM-as-judge (RAGEVAL-001)


class RAGEvalRunResponse(BaseModel):
    run_id: str
    status: str


class RAGEvalResultRow(BaseModel):
    slice_name: str
    metric_name: str
    metric_value: float
    threshold_value: Optional[float] = None
    passed: bool
    details: Optional[Dict] = None
    # Поля для LLM-as-judge (RAGEVAL-001)
    judge_faithfulness: Optional[float] = None
    judge_relevance: Optional[float] = None
    judge_completeness: Optional[float] = None
    judge_reasoning: Optional[str] = None
    judge_skipped: bool = False


class RAGEvalStatusResponse(BaseModel):
    run_id: str
    suite: str
    baseline_run_id: Optional[str] = None
    status: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    metrics: Optional[Dict] = None
    error_message: Optional[str] = None
    results: List[RAGEvalResultRow] = []


class RAGFeedbackRequest(BaseModel):
    request_id: str
    vote: Literal["helpful", "not_helpful"]
    comment: Optional[str] = None
    user_id: Optional[int] = None  # Опционально, для привязки к пользователю


class RAGFeedbackResponse(BaseModel):
    ok: bool
