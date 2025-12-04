"""
Плейсхолдер для сервиса загрузки документов.
"""

from sqlalchemy.orm import Session


class IngestionService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def enqueue_job(self, payload: dict) -> str:
        raise NotImplementedError


