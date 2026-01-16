from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from backend.api.deps import get_db_dep, require_api_key
from backend.schemas.common import (
    KnowledgeBaseInfo,
    KnowledgeSourceInfo,
    KnowledgeImportLogEntry,
    KnowledgeBaseCreate,
    KnowledgeBaseSettings,
)

# Временное использование существующих моделей и RAG-системы, позже будут перенесены.
from shared.database import KnowledgeBase, KnowledgeChunk, KnowledgeImportLog  # type: ignore
from shared.kb_settings import normalize_kb_settings, dump_kb_settings, default_kb_settings  # type: ignore
from sqlalchemy import func
from shared.rag_system import rag_system  # type: ignore


router = APIRouter(prefix="/knowledge-bases", tags=["knowledge"])


@router.get(
    "/",
    response_model=List[KnowledgeBaseInfo],
    summary="Список баз знаний",
    dependencies=[Depends(require_api_key)],
)
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


@router.post(
    "/",
    response_model=KnowledgeBaseInfo,
    summary="Создать новую базу знаний",
    dependencies=[Depends(require_api_key)],
)
def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    db: Session = Depends(get_db_dep),
) -> KnowledgeBaseInfo:
    default_settings = default_kb_settings()
    kb = KnowledgeBase(
        name=payload.name,
        description=payload.description,
        settings=dump_kb_settings(default_settings),
    )
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return KnowledgeBaseInfo(id=kb.id, name=kb.name, description=kb.description)


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


@router.post(
    "/{kb_id}/clear",
    summary="Очистить базу знаний (удалить все фрагменты и логи импорта)",
    dependencies=[Depends(require_api_key)],
)
def clear_knowledge_base_route(kb_id: int, db: Session = Depends(get_db_dep)) -> dict:
    # Используем существующую логику rag_system, чтобы гарантировать корректное удаление
    ok = rag_system.clear_knowledge_base(kb_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Failed to clear knowledge base")
    return {"status": "ok"}


@router.delete(
    "/{kb_id}",
    summary="Удалить базу знаний полностью",
    dependencies=[Depends(require_api_key)],
)
def delete_knowledge_base_route(kb_id: int, db: Session = Depends(get_db_dep)) -> dict:
    ok = rag_system.delete_knowledge_base(kb_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Failed to delete knowledge base")
    return {"status": "ok"}


@router.get(
    "/{kb_id}/import-log",
    response_model=List[KnowledgeImportLogEntry],
    summary="Журнал загрузок для базы знаний",
    dependencies=[Depends(require_api_key)],
)
def get_import_log(kb_id: int, db: Session = Depends(get_db_dep)) -> List[KnowledgeImportLogEntry]:
    logs = (
        db.query(KnowledgeImportLog)
        .filter(KnowledgeImportLog.knowledge_base_id == kb_id)
        .order_by(KnowledgeImportLog.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        KnowledgeImportLogEntry(
            created_at=log.created_at,
            username=log.username,
            user_telegram_id=log.user_telegram_id,
            action_type=log.action_type,
            source_path=log.source_path or "",
            total_chunks=log.total_chunks or 0,
        )
        for log in logs
    ]


@router.get(
    "/{kb_id}/settings",
    response_model=KnowledgeBaseSettings,
    summary="Получить настройки базы знаний",
    dependencies=[Depends(require_api_key)],
)
def get_kb_settings(kb_id: int, db: Session = Depends(get_db_dep)) -> KnowledgeBaseSettings:
    kb = db.query(KnowledgeBase).filter_by(id=kb_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    settings = normalize_kb_settings(kb.settings)
    return KnowledgeBaseSettings(settings=settings)


@router.put(
    "/{kb_id}/settings",
    response_model=KnowledgeBaseSettings,
    summary="Обновить настройки базы знаний",
    dependencies=[Depends(require_api_key)],
)
def update_kb_settings(
    kb_id: int,
    payload: KnowledgeBaseSettings,
    db: Session = Depends(get_db_dep),
) -> KnowledgeBaseSettings:
    kb = db.query(KnowledgeBase).filter_by(id=kb_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    merged = normalize_kb_settings(payload.settings)
    kb.settings = dump_kb_settings(merged)
    db.commit()
    return KnowledgeBaseSettings(settings=merged)

