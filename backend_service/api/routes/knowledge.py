from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend_service.api.deps import get_db_dep

# Временное использование существующих моделей, позже будут перенесены.
from database import KnowledgeBase  # type: ignore


router = APIRouter(prefix="/knowledge-bases", tags=["knowledge"])


@router.get("/", summary="Список баз знаний")
def list_knowledge_bases(db: Session = Depends(get_db_dep)) -> list[dict]:
    kbs = db.query(KnowledgeBase).all()
    return [
        {
            "id": kb.id,
            "name": kb.name,
            "description": kb.description,
        }
        for kb in kbs
    ]


