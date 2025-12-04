from typing import Optional, List

from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Глобальные настройки backend-сервиса.

    Все чувствительные значения берутся из переменных окружения / .env.
    """

    # Общие
    APP_NAME: str = "Telegram RAG Backend"
    API_V1_PREFIX: str = "/api/v1"

    # CORS / внешние клиенты
    BACKEND_CORS_ORIGINS: List[AnyHttpUrl] = []

    # БД
    DATABASE_URL: str = "sqlite:///./bot_database.db"

    # Интеграция с Telegram-ботом (для аудита / безопасности)
    TELEGRAM_BOT_TOKEN: Optional[str] = None

    class Config:
        env_file = ".env"
        case_sensitive = False
        # Backend использует только свои настройки, остальные (MYSQL_URL, OLLAMA_* и т.п.)
        # безопасно игнорируем, чтобы не было ValidationError при старте.
        extra = "ignore"


settings = Settings()


