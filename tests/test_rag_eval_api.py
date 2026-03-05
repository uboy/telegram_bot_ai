from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")

from fastapi import HTTPException

from backend.api.routes.rag import rag_eval_run, rag_eval_status
from backend.schemas.rag import RAGEvalRunRequest


def test_rag_eval_run_returns_queued(monkeypatch):
    from backend.api.routes import rag as rag_module

    monkeypatch.setattr(
        rag_module.rag_eval_service,
        "start_run",
        lambda **kwargs: "eval_20260305_abc123",  # noqa: ARG005
    )

    response = rag_eval_run(
        RAGEvalRunRequest(suite="rag-general-v1", slices=["en", "howto"]),
        db=object(),
    )

    assert response.run_id == "eval_20260305_abc123"
    assert response.status == "queued"


def test_rag_eval_status_returns_payload(monkeypatch):
    from backend.api.routes import rag as rag_module

    started_at = datetime(2026, 3, 5, 10, 0, 0, tzinfo=timezone.utc)
    finished_at = datetime(2026, 3, 5, 10, 1, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        rag_module.rag_eval_service,
        "get_run_status",
        lambda run_id: {
            "run_id": run_id,
            "suite_name": "rag-general-v1",
            "baseline_run_id": "baseline-1",
            "status": "completed",
            "started_at": started_at,
            "finished_at": finished_at,
            "metrics": {"total_cases": 2},
            "error_message": None,
            "results": [
                {
                    "slice_name": "overall",
                    "metric_name": "recall_at_10",
                    "metric_value": 1.0,
                    "threshold_value": 0.6,
                    "passed": True,
                    "details_json": '{"sample_size":2}',
                }
            ],
        },
    )

    response = rag_eval_status("eval_20260305_x1", db=object())

    assert response.run_id == "eval_20260305_x1"
    assert response.status == "completed"
    assert response.metrics == {"total_cases": 2}
    assert response.started_at == started_at.isoformat()
    assert len(response.results) == 1
    assert response.results[0].metric_name == "recall_at_10"
    assert response.results[0].details == {"sample_size": 2}


def test_rag_eval_status_not_found(monkeypatch):
    from backend.api.routes import rag as rag_module

    monkeypatch.setattr(rag_module.rag_eval_service, "get_run_status", lambda run_id: None)  # noqa: ARG005
    with pytest.raises(HTTPException) as exc:
        rag_eval_status("missing", db=object())
    assert exc.value.status_code == 404
