from fastapi import APIRouter

from backend.core.settings import settings


router = APIRouter(tags=["health"])


@router.get("/health", summary="Проверка состояния сервиса")
def health_check() -> dict:
    return {
        "status": "ok",
        "app": settings.APP_NAME,
    }


