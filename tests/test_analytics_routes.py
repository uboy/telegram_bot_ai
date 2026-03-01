from datetime import datetime

import pytest

pytest.importorskip("fastapi")

from fastapi import HTTPException

from backend.schemas.analytics import MessagePayload, QARequest
from backend.api.routes import analytics as analytics_routes


@pytest.mark.anyio
async def test_store_message_delegates_to_service(monkeypatch):
    monkeypatch.setattr(analytics_routes._analytics_service, "store_message", lambda payload: 123)
    payload = MessagePayload(
        chat_id="-1001",
        message_id=1,
        text="hello",
        timestamp="2025-01-01T00:00:00+00:00",
    )
    result = await analytics_routes.store_message(payload)
    assert result["status"] == "ok"
    assert result["id"] == 123


@pytest.mark.anyio
async def test_generate_digest_rejects_invalid_date():
    class DummyBackgroundTasks:
        def add_task(self, _fn):
            return None

    with pytest.raises(HTTPException) as excinfo:
        await analytics_routes.generate_digest(
            body=type(
                "R",
                (),
                {"chat_id": "-1001", "period_start": "bad-date", "period_end": "2025-01-01T00:00:00+00:00"},
            )(),
            background_tasks=DummyBackgroundTasks(),
        )
    assert excinfo.value.status_code == 400


@pytest.mark.anyio
async def test_question_answer_maps_sources(monkeypatch):
    async def _answer_question(*_args, **_kwargs):
        return {
            "answer": "ok",
            "confidence": "medium",
            "sources": [
                {
                    "message_id": 7,
                    "text": "src",
                    "author": "john",
                    "timestamp": datetime(2025, 1, 1).isoformat(),
                    "message_link": "https://t.me/c/1/7",
                    "thread_id": 12,
                    "score": 0.9,
                }
            ],
        }

    monkeypatch.setattr(analytics_routes._search_service, "answer_question", _answer_question)
    payload = QARequest(question="What happened?", chat_id="-1001")
    result = await analytics_routes.question_answer(payload)
    assert result.answer == "ok"
    assert result.confidence == "medium"
    assert len(result.sources) == 1
    assert result.sources[0].message_id == 7
