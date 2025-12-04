from typing import Generator

from fastapi import Depends
from sqlalchemy.orm import Session

from backend_service.core.database import get_db


def get_db_dep() -> Generator[Session, None, None]:
    """FastAPI dependency для доступа к БД."""
    with get_db() as db:
        yield db


