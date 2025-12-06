"""
Типы данных для передачи между слоями приложения.
Используются вместо ORM объектов для избежания проблем с session lifecycle.
"""
from dataclasses import dataclass
from typing import Optional, Any


@dataclass(frozen=True)
class UserContext:
    """Контекст пользователя для использования в handlers.
    
    ВАЖНО: Не возвращаем ORM объекты из функций, где закрывается session.
    Возвращаем DTO (dataclass) или dict.
    """
    telegram_id: str
    username: Optional[str]
    full_name: Optional[str]
    role: str
    approved: bool
    preferred_provider: Optional[str] = None
    preferred_model: Optional[str] = None
    preferred_image_model: Optional[str] = None


def normalize_telegram_id(value: Any) -> str:
    """Единый формат telegram_id по системе: всегда str."""
    return str(value) if value is not None else ""

