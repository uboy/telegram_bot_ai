from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend_service.api.deps import get_db_dep
from backend_service.schemas.rag import RAGQuery, RAGAnswer, RAGSource

# На данном этапе используем существующий rag_system как "заглушку".
# Позже логика будет перенесена внутрь backend_service.
from rag_system import rag_system  # type: ignore


router = APIRouter(prefix="/rag", tags=["rag"])


@router.post("/query", response_model=RAGAnswer, summary="Поиск ответа в базе знаний (RAG)")
def rag_query(payload: RAGQuery, db: Session = Depends(get_db_dep)) -> RAGAnswer:  # noqa: ARG001
    if not payload.query or not payload.query.strip():
        raise HTTPException(status_code=400, detail="query is required")

    kb_id = payload.knowledge_base_id

    # Минимальная интеграция с текущим rag_system
    results = rag_system.search(
        query=payload.query,
        knowledge_base_id=kb_id,
        top_k=payload.top_k or 10,
    ) or []

    if not results:
        return RAGAnswer(answer="", sources=[])

    # Пока не подключен AI- слой: возвращаем короткий конкатенированный текст
    snippets = []
    for chunk in results[:3]:
        content = (chunk.get("content") or "").strip()
        if content:
            snippets.append(content[:400])
    answer_text = "\n\n".join(snippets)

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

    return RAGAnswer(answer=answer_text, sources=sources)


