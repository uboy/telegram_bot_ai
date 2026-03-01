import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")

from shared import ai_providers


class DummyResponse:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_openai_provider_uses_model_override(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return DummyResponse(
            200,
            payload={"choices": [{"message": {"content": "ok"}}]},
        )

    monkeypatch.setattr(ai_providers.requests, "post", fake_post)

    provider = ai_providers.OpenAIProvider(
        api_key="sk-dummy-test-value-not-real-key",
        model="default-model",
        base_url="https://api.example.com/v1",
    )
    result = provider.query("hello", model="override-model")

    assert result == "ok"
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["json"]["model"] == "override-model"
    assert captured["json"]["messages"][0]["content"] == "hello"


def test_anthropic_provider_builds_messages_payload(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return DummyResponse(
            200,
            payload={"content": [{"type": "text", "text": "anthropic-ok"}]},
        )

    monkeypatch.setattr(ai_providers.requests, "post", fake_post)

    provider = ai_providers.AnthropicProvider(
        api_key="x-anthropic-dummy-test-key",
        model="claude-default",
        base_url="https://anthropic.example",
    )
    result = provider.query("hello", model="claude-override", temperature=0.2)

    assert result == "anthropic-ok"
    assert captured["url"] == "https://anthropic.example/v1/messages"
    assert captured["json"]["model"] == "claude-override"
    assert captured["json"]["messages"][0]["content"] == "hello"
    assert captured["json"]["temperature"] == 0.2


def test_manager_passes_model_override_to_any_provider():
    class EchoProvider(ai_providers.AIProvider):
        def __init__(self):
            super().__init__("echo")
            self.last_model = None

        def query(self, prompt: str, model=None, **kwargs) -> str:
            self.last_model = model
            return f"{model}:{prompt}"

        def query_multimodal(self, prompt: str, image_path=None, model=None, **kwargs) -> str:
            self.last_model = model
            return f"{model}:{prompt}:{image_path or ''}"

    manager = ai_providers.AIProviderManager()
    provider = EchoProvider()
    manager.register_provider("echo", provider)

    result = manager.query("ping", provider_name="echo", model="model-x")
    assert result == "model-x:ping"
    assert provider.last_model == "model-x"


def test_init_default_providers_registers_configured_apis(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x-anthropic")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x-deepseek")
    monkeypatch.setenv("OPEN_WEBUI_BASE_URL", "http://localhost:3000/api")
    monkeypatch.setenv("OPEN_WEBUI_MODEL", "llama3.1:8b")
    monkeypatch.setenv("AI_DEFAULT_PROVIDER", "deepseek")

    old_providers = dict(ai_providers.ai_manager.providers)
    old_current = ai_providers.ai_manager.current_provider
    old_default = ai_providers.ai_manager.default_provider
    try:
        ai_providers.init_default_providers()
        providers = set(ai_providers.ai_manager.list_providers())

        assert "ollama" in providers
        assert "openai" in providers
        assert "anthropic" in providers
        assert "deepseek" in providers
        assert "open_webui" in providers
        assert ai_providers.ai_manager.current_provider == "deepseek"
        assert ai_providers.ai_manager.default_provider == "deepseek"
    finally:
        ai_providers.ai_manager.providers = old_providers
        ai_providers.ai_manager.current_provider = old_current
        ai_providers.ai_manager.default_provider = old_default
