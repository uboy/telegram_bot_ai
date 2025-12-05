"""
Плейсхолдер для RAG-сервисов backend-приложения.
"""

from sqlalchemy.orm import Session


class RAGService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def query(self, query: str) -> dict:
        raise NotImplementedError


