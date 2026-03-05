import pytest

pytest.importorskip("fastapi")

from fastapi.routing import APIRoute

from backend.app import create_app


def test_critical_api_routes_exist():
    app = create_app()
    registered = {(method, route.path) for route in app.routes if isinstance(route, APIRoute) for method in route.methods}

    expected = {
        ("POST", "/api/v1/rag/query"),
        ("GET", "/api/v1/rag/diagnostics/{request_id}"),
        ("POST", "/api/v1/rag/eval/run"),
        ("GET", "/api/v1/rag/eval/{run_id}"),
        ("POST", "/api/v1/rag/summary"),
        ("POST", "/api/v1/rag/reload-models"),
        ("POST", "/api/v1/asr/transcribe"),
        ("GET", "/api/v1/asr/jobs/{job_id}"),
        ("GET", "/api/v1/asr/settings"),
        ("PUT", "/api/v1/asr/settings"),
        ("POST", "/api/v1/ingestion/code-path"),
        ("POST", "/api/v1/ingestion/code-git"),
        ("GET", "/api/v1/jobs/{job_id}"),
        ("GET", "/api/v1/knowledge-bases/{kb_id}/settings"),
        ("PUT", "/api/v1/knowledge-bases/{kb_id}/settings"),
        ("POST", "/api/v1/analytics/messages"),
        ("POST", "/api/v1/analytics/digests/generate"),
        ("POST", "/api/v1/analytics/qa"),
        ("GET", "/api/v1/analytics/stats/{chat_id}"),
    }

    missing = expected - registered
    assert not missing, f"Missing API routes: {sorted(missing)}"
