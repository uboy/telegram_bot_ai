from typing import Generator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.settings import settings


def get_db_dep() -> Generator[Session, None, None]:
    """FastAPI dependency для доступа к БД."""
    with get_db() as db:
        yield db


def require_api_key(x_api_key: str = Header(default="", alias="X-API-Key")) -> None:
    """Проверка простого API-ключа для запросов от бота.

    Если BACKEND_API_KEY не задан, проверка отключена (для dev-среды).
    """
    expected = settings.BACKEND_API_KEY
    if not expected:
        # Авторизация отключена
        return

    if not x_api_key or x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )

