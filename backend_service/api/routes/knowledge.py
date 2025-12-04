from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

from backend_service.api.deps import get_db_dep
from backend_service.schemas.common import KnowledgeBaseInfo, KnowledgeSourceInfo

# Временное использование существующих моделей, позже будут перенесены.
from database import KnowledgeBase, KnowledgeChunk  # type: ignore
from sqlalchemy import func


router = APIRouter(prefix="/knowledge-bases", tags=["knowledge"])


@router.get("/", response_model=List[KnowledgeBaseInfo], summary="Список баз знаний")
def list_knowledge_bases(db: Session = Depends(get_db_dep)) -> List[KnowledgeBaseInfo]:
    kbs = db.query(KnowledgeBase).all()
    return [
        KnowledgeBaseInfo(
            id=kb.id,
            name=kb.name,
            description=kb.description,
        )
        for kb in kbs
    ]


@router.get(
    "/{kb_id}/sources",
    response_model=List[KnowledgeSourceInfo],
    summary="Список источников в базе знаний с датой последнего обновления",
)
def list_knowledge_sources(
    kb_id: int,
    db: Session = Depends(get_db_dep),
) -> List[KnowledgeSourceInfo]:
    """
    Возвращает агрегированный список источников (source_path + source_type)
    с количеством чанков и датой последнего обновления.
    Отсортировано по дате последнего обновления (DESC).
    """
    rows = (
        db.query(
            KnowledgeChunk.source_path,
            KnowledgeChunk.source_type,
            func.max(KnowledgeChunk.created_at).label("last_updated"),
            func.count(KnowledgeChunk.id).label("chunks_count"),
        )
        .filter(KnowledgeChunk.knowledge_base_id == kb_id)
        .group_by(KnowledgeChunk.source_path, KnowledgeChunk.source_type)
        .order_by(func.max(KnowledgeChunk.created_at).desc())
        .all()
    )

    return [
        KnowledgeSourceInfo(
            source_path=row.source_path or "",
            source_type=row.source_type or "",
            chunks_count=int(row.chunks_count or 0),
            last_updated=row.last_updated,
        )
        for row in rows
        if row.source_path
    ]

