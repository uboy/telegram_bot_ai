from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, Any

from backend.api.deps import get_db_dep, require_api_key
from backend.schemas.rag import RAGQuery, RAGAnswer, RAGSource

# Используем существующую RAG-систему и AI-менеджер из основного проекта.
from shared.rag_system import rag_system  # type: ignore
from shared.ai_providers import ai_manager  # type: ignore
from shared.utils import create_prompt_with_language  # type: ignore


router = APIRouter(prefix="/rag", tags=["rag"])


@router.post(
    "/query",
    response_model=RAGAnswer,
    summary="Поиск ответа в базе знаний (RAG)",
    dependencies=[Depends(require_api_key)],
)
def rag_query(payload: RAGQuery, db: Session = Depends(get_db_dep)) -> RAGAnswer:  # noqa: ARG001
    if not payload.query or not payload.query.strip():
        raise HTTPException(status_code=400, detail="query is required")

    kb_id = payload.knowledge_base_id

    # Настройки RAG
    try:
        from shared.config import (  # type: ignore
            RAG_TOP_K,
            RAG_CONTEXT_LENGTH,
            RAG_ENABLE_CITATIONS,
            RAG_MIN_RERANK_SCORE,
        )
        top_k_search = payload.top_k or RAG_TOP_K
        top_k_for_context = RAG_TOP_K
        context_length = RAG_CONTEXT_LENGTH
        enable_citations = RAG_ENABLE_CITATIONS
        min_rerank_score = RAG_MIN_RERANK_SCORE
    except Exception:  # noqa: BLE001
        top_k_search = payload.top_k or 10
        top_k_for_context = 8
        context_length = 1200
        enable_citations = True
        min_rerank_score = 0.0

    # Поиск кандидатов в RAG (dense + keyword + optional rerank)
    results = rag_system.search(
        query=payload.query,
        knowledge_base_id=kb_id,
        top_k=top_k_search,
    ) or []

    if not results:
        # Нет релевантных фрагментов в БЗ – честно говорим, что ответа нет
        return RAGAnswer(answer="", sources=[])

    # Анти-галлюцинации: если есть reranker и все score ниже порога – считаем, что ответа нет
    best_score = max(float(r.get("rerank_score", 0.0)) for r in results)
    if min_rerank_score > 0.0 and best_score < min_rerank_score:
        return RAGAnswer(answer="", sources=[])

    # Формируем контекст для LLM на основе найденных фрагментов
    context_parts: list[str] = []
    for idx, r in enumerate(results[:top_k_for_context], start=1):
        source_path = r.get("source_path") or ""
        meta = r.get("metadata") or {}
        title = meta.get("title") or source_path or "Без названия"

        # Формируем source_id для inline-citations
        if source_path and ".keep" not in source_path.lower():
            if "::" in source_path:
                source_id = source_path.split("::")[-1]
            elif "/" in source_path:
                source_id = source_path.split("/")[-1]
            else:
                source_id = source_path
            source_id = source_id.rsplit(".", 1)[0] if "." in source_id else source_id
        else:
            source_id = title.replace(" ", "_").lower()[:50]

        content = r.get("content") or ""
        content_preview = content[:context_length]
        if len(content) > context_length:
            content_preview += "..."

        if enable_citations:
            context_parts.append(f"<source_id>{source_id}</source_id>\n{content_preview}")
        else:
            header = f"=== Источник {idx}: {title} ==="
            context_parts.append(f"{header}\n{content_preview}")

    context_text = "\n\n".join(context_parts)

    # Вызываем LLM через общий ai_manager
    prompt = create_prompt_with_language(
        payload.query,
        context_text,
        task="answer",
        enable_citations=enable_citations,
    )

    model = None  # Пока без персональных настроек, используется дефолт провайдера
    ai_answer = ai_manager.query(prompt)

    # Собираем список источников для ответа
    sources: list[RAGSource] = []
    for chunk in results:
        metadata = chunk.get("metadata") or {}
        sources.append(
            RAGSource(
                source_path=chunk.get("source_path") or metadata.get("source_path") or "",
                source_type=chunk.get("source_type") or metadata.get("source_type") or "",
                score=float(chunk.get("distance", 0.0)),
            )
        )

    return RAGAnswer(answer=ai_answer, sources=sources)


@router.post(
    "/reload-models",
    summary="Перезагрузить модели RAG",
    dependencies=[Depends(require_api_key)],
)
def rag_reload_models(db: Session = Depends(get_db_dep)) -> Dict[str, Any]:  # noqa: ARG001
    """
    Перезагрузить модели эмбеддингов и ранкинга в RAG-системе.
    Проксирует существующий метод rag_system.reload_models().
    """
    result = rag_system.reload_models()
    # Ожидается словарь с ключами 'embedding' и 'reranker' (bool)
    return {
        "embedding": bool(result.get("embedding")),
        "reranker": bool(result.get("reranker")),
    }

