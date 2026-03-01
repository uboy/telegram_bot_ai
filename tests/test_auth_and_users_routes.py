import pytest

pytest.importorskip("fastapi")

from backend.api.routes import auth as auth_routes
from backend.api.routes import users as users_routes


class SimpleUser:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id")
        self.telegram_id = kwargs.get("telegram_id")
        self.username = kwargs.get("username")
        self.full_name = kwargs.get("full_name")
        self.phone = kwargs.get("phone")
        self.role = kwargs.get("role")
        self.approved = kwargs.get("approved")


class AuthSession:
    def __init__(self, user=None):
        self.user = user
        self.added = None

    def query(self, _model):
        return self

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return self.user

    def add(self, user):
        self.added = user
        self.user = user

    def commit(self):
        return None

    def refresh(self, user):
        if user.id is None:
            user.id = 1


def test_auth_telegram_creates_admin_user(monkeypatch):
    monkeypatch.setattr(auth_routes, "User", SimpleUser)
    monkeypatch.setattr(auth_routes, "ADMIN_IDS", [123])

    db = AuthSession(user=None)
    result = auth_routes.auth_telegram(telegram_id="123", username="u", full_name="User", db=db)
    assert result.role == "admin"
    assert result.approved is True


def test_auth_telegram_updates_existing_user(monkeypatch):
    monkeypatch.setattr(auth_routes, "User", SimpleUser)
    monkeypatch.setattr(auth_routes, "ADMIN_IDS", [])
    existing = SimpleUser(id=9, telegram_id="9", username="old", full_name=None, role="user", approved=False)
    db = AuthSession(user=existing)

    result = auth_routes.auth_telegram(telegram_id="9", username="new", full_name="New Name", db=db)
    assert result.username == "new"
    assert result.full_name == "New Name"


class UsersSession:
    def __init__(self, user):
        self.user = user
        self.deleted = False

    def query(self, _model):
        return self

    def get(self, user_id):
        if self.user and self.user.id == user_id:
            return self.user
        return None

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return [self.user] if self.user else []

    def commit(self):
        return None

    def refresh(self, _user):
        return None

    def delete(self, _user):
        self.deleted = True


def test_toggle_user_role_approves_unapproved_user(monkeypatch):
    monkeypatch.setattr(users_routes, "User", SimpleUser)
    user = SimpleUser(id=1, telegram_id="1", username="u1", role="user", approved=False)
    db = UsersSession(user)

    result = users_routes.toggle_user_role(1, db=db)
    assert result.approved is True
    assert result.role == "user"


def test_delete_user_returns_ok(monkeypatch):
    monkeypatch.setattr(users_routes, "User", SimpleUser)
    user = SimpleUser(id=1, telegram_id="1", username="u1", role="user", approved=True)
    db = UsersSession(user)

    result = users_routes.delete_user(1, db=db)
    assert result["status"] == "ok"
    assert db.deleted is True
