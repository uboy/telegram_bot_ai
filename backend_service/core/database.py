from contextlib import contextmanager
from typing import Generator

from sqlalchemy.orm import Session as SASession

# Используем уже настроенный движок/сессию из существующего модуля `database`,
# чтобы backend и Telegram-бот гарантированно работали с одной и той же БД.
from database import Session as LegacySession  # type: ignore


@contextmanager
def get_db() -> Generator[SASession, None, None]:
    """Синхронная сессия БД (на первом этапе достаточно)."""
    db: SASession = LegacySession()
    try:
        yield db
    finally:
        db.close()


