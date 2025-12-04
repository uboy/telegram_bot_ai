"""
Репозиторий баз знаний (плейсхолдер).
"""

from sqlalchemy.orm import Session


class KnowledgeRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_bases(self) -> list:
        raise NotImplementedError


