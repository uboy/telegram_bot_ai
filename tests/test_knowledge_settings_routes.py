import pytest

pytest.importorskip("fastapi")

from fastapi import HTTPException

from backend.api.routes import knowledge as knowledge_routes
from backend.schemas.common import KnowledgeBaseSettings


class DummyKB:
    def __init__(self, kb_id=1, settings=None):
        self.id = kb_id
        self.settings = settings or {}


class DummyDB:
    def __init__(self, kb=None):
        self.kb = kb
        self.committed = False

    def query(self, _model):
        return self

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return self.kb

    def commit(self):
        self.committed = True


def test_get_kb_settings_not_found():
    with pytest.raises(HTTPException) as excinfo:
        knowledge_routes.get_kb_settings(7, db=DummyDB(kb=None))
    assert excinfo.value.status_code == 404


def test_update_kb_settings_updates_payload(monkeypatch):
    monkeypatch.setattr(knowledge_routes, "normalize_kb_settings", lambda settings: settings or {})
    monkeypatch.setattr(knowledge_routes, "dump_kb_settings", lambda settings: settings)

    kb = DummyKB(kb_id=1, settings={"rag": {"single_page_mode": False}})
    db = DummyDB(kb=kb)
    payload = KnowledgeBaseSettings(settings={"rag": {"single_page_mode": True}})

    result = knowledge_routes.update_kb_settings(1, payload=payload, db=db)
    assert result.settings["rag"]["single_page_mode"] is True
    assert db.committed is True
