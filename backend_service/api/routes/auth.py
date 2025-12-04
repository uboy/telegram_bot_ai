from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend_service.api.deps import get_db_dep, require_api_key
from backend_service.schemas.user import UserOut

# Временно используем общую модель User из database.py
from database import User  # type: ignore

try:
    from config import ADMIN_IDS  # type: ignore
except Exception:  # noqa: BLE001
    import os

    ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
    ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip()]


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/telegram",
    response_model=UserOut,
    summary="Создать/обновить пользователя по данным из Telegram и вернуть его профиль",
)
def auth_telegram(
    telegram_id: str,
    username: str | None = None,
    full_name: str | None = None,
    db: Session = Depends(get_db_dep),
) -> UserOut:
    if not telegram_id:
        raise HTTPException(status_code=400, detail="telegram_id is required")

    is_admin = False
    try:
        tid_int = int(telegram_id)
        is_admin = tid_int in ADMIN_IDS
    except Exception:  # noqa: BLE001
        # Если telegram_id не приводится к int, просто считаем, что не админ
        is_admin = False

    user = db.query(User).filter_by(telegram_id=telegram_id).first()

    if not user:
        # Новый пользователь
        user = User(
            telegram_id=telegram_id,
            username=username or telegram_id,
            full_name=full_name,
            phone=None,
            approved=is_admin,
            role="admin" if is_admin else "user",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        # Обновляем базовые поля, если они изменились
        changed = False
        if username and user.username != username:
            user.username = username
            changed = True
        if full_name and getattr(user, "full_name", None) != full_name:
            user.full_name = full_name
            changed = True

        # Если этот пользователь в списке админов — гарантируем ему права админа и approved
        if is_admin and (not user.approved or user.role != "admin"):
            user.approved = True
            user.role = "admin"
            changed = True

        if changed:
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


