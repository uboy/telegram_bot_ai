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
    origin: Optional[str] = None
    channel: Optional[str] = None
    channel_rank: Optional[int] = None
    fusion_rank: Optional[int] = None
    fusion_score: Optional[str] = None
    rerank_delta: Optional[str] = None
    metadata: Optional[Dict] = None
    content_preview: Optional[str] = None


class RAGDiagnosticsResponse(BaseModel):
    request_id: str
    query: str
    knowledge_base_id: Optional[int] = None
    intent: Optional[str] = None
    orchestrator_mode: Optional[str] = None
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


