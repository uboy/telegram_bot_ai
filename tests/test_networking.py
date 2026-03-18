import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("DB_PATH", "data/test-networking.db")
os.environ["MYSQL_URL"] = ""

import shared.networking as networking
from frontend.backend_client import BackendClient


def test_get_proxy_env_settings_normalizes_blank_values(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", " http://proxy.local:8080 ")
    monkeypatch.setenv("HTTPS_PROXY", " ")
    monkeypatch.setenv("ALL_PROXY", "")
    monkeypatch.setenv("NO_PROXY", " backend,qdrant ")

    settings = networking.get_proxy_env_settings()

    assert settings == {
        "HTTP_PROXY": "http://proxy.local:8080",
        "HTTPS_PROXY": None,
        "ALL_PROXY": None,
        "NO_PROXY": "backend,qdrant",
    }


def test_build_httpx_client_enables_trust_env():
    client = networking.build_httpx_client(timeout=5.0, headers={"X-Test": "1"})
    try:
        assert getattr(client, "_trust_env", None) is True
        assert client.headers["X-Test"] == "1"
    finally:
        client.close()


def test_build_telegram_request_prefers_https_proxy(monkeypatch):
    captured = {}

    class DummyRequest:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(networking, "HTTPXRequest", DummyRequest)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.local:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://secure-proxy.local:8443")

    networking.build_telegram_request()

    assert captured["proxy"] == "http://secure-proxy.local:8443"
    assert captured["httpx_kwargs"] == {"trust_env": True}


def test_backend_client_uses_shared_httpx_builder(monkeypatch):
    captured = {}

    class DummyClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url):  # noqa: ARG002
            raise RuntimeError("stop")

    def fake_build_httpx_client(**kwargs):
        captured.update(kwargs)
        return DummyClient()

    monkeypatch.setattr("frontend.backend_client.build_httpx_client", fake_build_httpx_client)

    client = BackendClient(base_url="http://backend:8000", timeout=7.0)
    try:
        client.list_users()
    except RuntimeError:
        pass

    assert captured["timeout"] == 7.0
    assert captured["headers"] == {}
