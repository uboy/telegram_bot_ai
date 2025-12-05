"""
Плейсхолдер для сервиса работы с пользователями.
"""

from sqlalchemy.orm import Session


class UserService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_users(self) -> list:
        raise NotImplementedError


