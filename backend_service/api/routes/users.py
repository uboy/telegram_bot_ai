from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend_service.api.deps import get_db_dep

# На первом шаге используем существующую модель User из database.py,
# позже она будет перенесена в backend_service.models.
from database import User  # type: ignore


router = APIRouter(prefix="/users", tags=["users"])


@router.get("/", summary="Список пользователей (минимальный)")
def list_users(db: Session = Depends(get_db_dep)) -> list[dict]:
    users = db.query(User).all()
    return [
        {
            "id": u.id,
            "telegram_id": u.telegram_id,
            "username": u.username,
            "role": u.role,
            "approved": u.approved,
        }
        for u in users
    ]


