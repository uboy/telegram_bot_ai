from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend_service.api.deps import get_db_dep, require_api_key
from backend_service.schemas.user import UserOut

# На первом шаге используем существующую модель User из database.py,
# позже она будет перенесена в backend_service.models.
from database import User  # type: ignore


router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "/",
    response_model=list[UserOut],
    summary="Список пользователей",
    dependencies=[Depends(require_api_key)],
)
def list_users(db: Session = Depends(get_db_dep)) -> list[UserOut]:
    users = db.query(User).order_by(User.id.asc()).all()
    return [
        UserOut(
            id=u.id,
            telegram_id=u.telegram_id,
            username=u.username,
            full_name=getattr(u, "full_name", None),
            phone=getattr(u, "phone", None),
            role=u.role or "user",
            approved=bool(u.approved),
        )
        for u in users
    ]


@router.post(
    "/{user_id}/toggle-role",
    response_model=UserOut,
    summary="Одобрить/сменить роль пользователя",
    dependencies=[Depends(require_api_key)],
)
def toggle_user_role(user_id: int, db: Session = Depends(get_db_dep)) -> UserOut:
    user = db.query(User).get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Если пользователь ещё не одобрен — одобряем и ставим роль user
    if not user.approved:
        user.approved = True
        user.role = "user"
    else:
        # Меняем роль на противоположную между user/admin
        user.role = "admin" if (user.role or "user") == "user" else "user"

    db.commit()
    db.refresh(user)

    return UserOut(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        full_name=getattr(user, "full_name", None),
        phone=getattr(user, "phone", None),
        role=user.role or "user",
        approved=bool(user.approved),
    )


@router.delete(
    "/{user_id}",
    summary="Удалить пользователя",
    dependencies=[Depends(require_api_key)],
)
def delete_user(user_id: int, db: Session = Depends(get_db_dep)) -> dict:
    user = db.query(User).get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    db.delete(user)
    db.commit()

    return {"status": "ok"}

