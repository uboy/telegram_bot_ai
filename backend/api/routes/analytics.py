"""Chat analytics API endpoints."""
from datetime import datetime
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, BackgroundTasks
from typing import Optional

from backend.schemas.analytics import (
    MessagePayload,
    MessageBatchPayload,
    AnalyticsConfigUpdate,
    AnalyticsConfigResponse,
    DigestGenerateRequest,
    SearchRequest,
    QARequest,
    ChatDigestResponse,
    ChatDigestThemeResponse,
    SearchResultResponse,
    SearchResultItem,
    QAResponse,
    ChatStatsResponse,
    ImportStatusResponse,
)
from backend.services.chat_analytics_service import ChatAnalyticsService
from backend.services.chat_search_service import ChatSearchService
from backend.services.history_import_service import HistoryImportService

router = APIRouter(prefix="/analytics", tags=["analytics"])

_analytics_service = ChatAnalyticsService()
_search_service = ChatSearchService()
_import_service = HistoryImportService()


# ── Messages ───────────────────────────────────────────────────────────

@router.post("/messages")
async def store_message(payload: MessagePayload):
    """Store a single message from bot collector."""
    msg_id = _analytics_service.store_message(payload.model_dump())
    return {"status": "ok", "id": msg_id}


@router.post("/messages/batch")
async def store_messages_batch(payload: MessageBatchPayload):
    """Batch store messages."""
    stored = 0
    skipped = 0
    for msg in payload.messages:
        result = _analytics_service.store_message(msg.model_dump())
        if result > 0:
            stored += 1
        else:
            skipped += 1
    return {"status": "ok", "stored": stored, "skipped": skipped}


# ── Config CRUD ────────────────────────────────────────────────────────

@router.get("/configs")
async def list_configs():
    """List all analytics configs."""
    configs = _analytics_service.list_configs()
    return [AnalyticsConfigResponse.model_validate(c) for c in configs]


@router.get("/configs/{chat_id}")
async def get_config(chat_id: str):
    """Get analytics config for a chat."""
    config = _analytics_service.get_config(chat_id)
    if not config:
        raise HTTPException(404, "Config not found")
    return AnalyticsConfigResponse.model_validate(config)


@router.put("/configs/{chat_id}")
async def upsert_config(chat_id: str, body: AnalyticsConfigUpdate):
    """Create or update analytics config."""
    config = _analytics_service.upsert_config(chat_id, body.model_dump(exclude_none=True))
    return AnalyticsConfigResponse.model_validate(config)


@router.delete("/configs/{chat_id}")
async def delete_config(chat_id: str):
    """Delete analytics config."""
    deleted = _analytics_service.delete_config(chat_id)
    if not deleted:
        raise HTTPException(404, "Config not found")
    return {"status": "deleted"}


# ── Digests ────────────────────────────────────────────────────────────

@router.post("/digests/generate")
async def generate_digest(body: DigestGenerateRequest,
                          background_tasks: BackgroundTasks):
    """Trigger digest generation (runs in background)."""
    try:
        period_start = datetime.fromisoformat(body.period_start.replace('Z', '+00:00'))
        period_end = datetime.fromisoformat(body.period_end.replace('Z', '+00:00'))
    except ValueError as e:
        raise HTTPException(400, f"Invalid date format: {e}")

    # Start in background
    from shared.database import get_session, ChatDigest
    with get_session() as session:
        digest = ChatDigest(
            chat_id=body.chat_id,
            period_start=period_start,
            period_end=period_end,
            status='pending',
        )
        session.add(digest)
        session.flush()
        digest_id = digest.id

    async def _run():
        # Delete the placeholder and let run_analysis create its own
        from shared.database import get_session as gs, ChatDigest as CD
        with gs() as s:
            d = s.query(CD).get(digest_id)
            if d:
                s.delete(d)
        await _analytics_service.run_analysis(body.chat_id, period_start, period_end)

    background_tasks.add_task(_run)
    return {"digest_id": digest_id, "status": "pending"}


@router.get("/digests/{digest_id}")
async def get_digest(digest_id: int):
    """Get a generated digest with themes."""
    from shared.database import get_session, ChatDigest
    with get_session() as session:
        digest = session.query(ChatDigest).get(digest_id)
        if not digest:
            raise HTTPException(404, "Digest not found")
        return ChatDigestResponse.model_validate(digest)


@router.get("/digests")
async def list_digests(chat_id: Optional[str] = None,
                       status: Optional[str] = None,
                       limit: int = 20, offset: int = 0):
    """List digests, optionally filtered by chat_id and status."""
    from shared.database import get_session, ChatDigest
    with get_session() as session:
        query = session.query(ChatDigest)
        if chat_id:
            query = query.filter(ChatDigest.chat_id == chat_id)
        if status:
            query = query.filter(ChatDigest.status == status)
        query = query.order_by(ChatDigest.created_at.desc())
        digests = query.offset(offset).limit(limit).all()
        return [ChatDigestResponse.model_validate(d) for d in digests]


# ── Search & Q&A ──────────────────────────────────────────────────────

@router.post("/search")
async def search_messages(body: SearchRequest):
    """Hybrid search over chat messages."""
    filters = {}
    if body.thread_id is not None:
        filters['thread_id'] = body.thread_id
    if body.author_telegram_id:
        filters['author_telegram_id'] = body.author_telegram_id
    if body.period_start:
        filters['period_start'] = datetime.fromisoformat(
            body.period_start.replace('Z', '+00:00'))
    if body.period_end:
        filters['period_end'] = datetime.fromisoformat(
            body.period_end.replace('Z', '+00:00'))

    results = _search_service.search(
        body.query, body.chat_id, filters=filters or None, top_k=body.top_k)

    return SearchResultResponse(
        results=[SearchResultItem(**r) for r in results],
        total_found=len(results),
    )


@router.post("/qa")
async def question_answer(body: QARequest):
    """Answer a question from chat history."""
    filters = {}
    if body.thread_id is not None:
        filters['thread_id'] = body.thread_id
    if body.period_start:
        filters['period_start'] = datetime.fromisoformat(
            body.period_start.replace('Z', '+00:00'))
    if body.period_end:
        filters['period_end'] = datetime.fromisoformat(
            body.period_end.replace('Z', '+00:00'))

    result = await _search_service.answer_question(
        body.question, body.chat_id, filters=filters or None)

    sources = [SearchResultItem(
        message_id=s.get('message_id', 0),
        text=s.get('text', ''),
        author=s.get('author'),
        timestamp=s.get('timestamp', ''),
        message_link=s.get('message_link'),
        thread_id=s.get('thread_id'),
        score=s.get('score', 0.0),
    ) for s in result.get('sources', [])]

    return QAResponse(
        answer=result.get('answer', ''),
        sources=sources,
        confidence=result.get('confidence', 'low'),
    )


# ── Import ─────────────────────────────────────────────────────────────

@router.post("/import")
async def import_history(
    file: UploadFile = File(...),
    chat_id: str = Form(...),
    format_hint: Optional[str] = Form(None),
    user_telegram_id: Optional[str] = Form(None),
):
    """Import chat history from file (multipart upload)."""
    content = await file.read()
    result = _import_service.import_history(
        file_content=content,
        chat_id=chat_id,
        format_hint=format_hint,
        filename=file.filename,
        user_telegram_id=user_telegram_id,
    )
    if result.get('status') == 'failed':
        raise HTTPException(400, result.get('error', 'Import failed'))
    return result


@router.get("/import/{import_id}")
async def get_import_status(import_id: int):
    """Check import job status."""
    status = _import_service.get_import_status(import_id)
    if not status:
        raise HTTPException(404, "Import not found")
    return status


# ── Stats ──────────────────────────────────────────────────────────────

@router.get("/stats/{chat_id}")
async def get_stats(chat_id: str,
                    period_start: Optional[str] = None,
                    period_end: Optional[str] = None):
    """Get message statistics for a chat."""
    from shared.database import get_session, ChatMessage
    from sqlalchemy import func

    with get_session() as session:
        query = session.query(ChatMessage).filter(ChatMessage.chat_id == chat_id)

        ps = None
        pe = None
        if period_start:
            ps = datetime.fromisoformat(period_start.replace('Z', '+00:00'))
            query = query.filter(ChatMessage.timestamp >= ps)
        if period_end:
            pe = datetime.fromisoformat(period_end.replace('Z', '+00:00'))
            query = query.filter(ChatMessage.timestamp <= pe)

        total = query.count()
        unique_authors = session.query(
            func.count(func.distinct(ChatMessage.author_telegram_id))
        ).filter(
            ChatMessage.chat_id == chat_id,
            *([ChatMessage.timestamp >= ps] if ps else []),
            *([ChatMessage.timestamp <= pe] if pe else []),
        ).scalar() or 0

        active_threads = session.query(
            func.count(func.distinct(ChatMessage.thread_id))
        ).filter(
            ChatMessage.chat_id == chat_id,
            ChatMessage.thread_id.isnot(None),
            *([ChatMessage.timestamp >= ps] if ps else []),
            *([ChatMessage.timestamp <= pe] if pe else []),
        ).scalar() or 0

        # Messages per day
        if ps and pe:
            days = max((pe - ps).days, 1)
        else:
            first = session.query(func.min(ChatMessage.timestamp)).filter(
                ChatMessage.chat_id == chat_id).scalar()
            last = session.query(func.max(ChatMessage.timestamp)).filter(
                ChatMessage.chat_id == chat_id).scalar()
            days = max((last - first).days, 1) if first and last else 1

        messages_per_day = round(total / days, 2) if days else 0

        # Top authors
        top_authors_q = session.query(
            ChatMessage.author_display_name,
            func.count(ChatMessage.id).label('cnt')
        ).filter(
            ChatMessage.chat_id == chat_id,
            *([ChatMessage.timestamp >= ps] if ps else []),
            *([ChatMessage.timestamp <= pe] if pe else []),
        ).group_by(
            ChatMessage.author_display_name
        ).order_by(func.count(ChatMessage.id).desc()).limit(10).all()

        top_authors = [{"name": a[0] or 'Unknown', "count": a[1]}
                       for a in top_authors_q]

        period = {}
        if ps:
            period['start'] = ps.isoformat()
        if pe:
            period['end'] = pe.isoformat()

        return ChatStatsResponse(
            total_messages=total,
            unique_authors=unique_authors,
            active_threads=active_threads,
            messages_per_day=messages_per_day,
            top_authors=top_authors,
            period=period,
        )
