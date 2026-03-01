import pytest

pytest.importorskip("fastapi")

from fastapi.routing import APIRoute

from backend.api.deps import require_api_key
from backend.app import create_app


def _requires_api_key(route: APIRoute) -> bool:
    return any(getattr(dep, "call", None) is require_api_key for dep in route.dependant.dependencies)


def test_protected_routes_require_api_key():
    app = create_app()
    protected_prefixes = (
        "/api/v1/users",
        "/api/v1/knowledge-bases",
        "/api/v1/ingestion",
        "/api/v1/jobs",
        "/api/v1/rag",
        "/api/v1/asr",
        "/api/v1/analytics",
    )

    protected_routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith(protected_prefixes)
    ]

    assert protected_routes, "Expected protected routes to exist"
    for route in protected_routes:
        assert _requires_api_key(route), f"Route is not protected by API key: {route.path}"


def test_public_routes_do_not_require_api_key():
    app = create_app()
    public_paths = {"/api/v1/health", "/api/v1/auth/telegram"}

    public_routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path in public_paths
    ]
    assert public_routes, "Expected public routes to exist"

    for route in public_routes:
        assert not _requires_api_key(route), f"Route should stay public: {route.path}"
