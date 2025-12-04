"""
Репозиторий пользователей (плейсхолдер).
"""

from sqlalchemy.orm import Session


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_users(self) -> list:
        raise NotImplementedError


