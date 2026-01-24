"""
Типы данных для передачи между слоями приложения.
Используются вместо ORM объектов для избежания проблем с session lifecycle.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Any, List, Dict


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


@dataclass(frozen=True)
class LoadedDocument:
    source_type: str
    source_path: str
    content: str
    metadata: Dict[str, Any]
    language: Optional[str] = None


@dataclass(frozen=True)
class Chunk:
    content: str
    metadata: Dict[str, Any]
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None


@dataclass(frozen=True)
class SearchFilters:
    kb_id: Optional[int] = None
    document_classes: Optional[List[str]] = None
    languages: Optional[List[str]] = None
    source_types: Optional[List[str]] = None
    path_prefixes: Optional[List[str]] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None


@dataclass(frozen=True)
class SearchResult:
    content: str
    score: float
    source_path: str
    metadata: Dict[str, Any]


@dataclass(frozen=True)
class JobStatus:
    job_id: str
    status: str
    progress: float = 0.0
    stage: Optional[str] = None
    error: Optional[str] = None


def normalize_telegram_id(value: Any) -> str:
    """Единый формат telegram_id по системе: всегда str."""
    return str(value) if value is not None else ""

