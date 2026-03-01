import os
from dataclasses import dataclass

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")

from shared import ai_metrics


@dataclass
class _Row:
    latency_ms: int
    prompt_tokens_est: int
    context_tokens_est: int


def test_estimate_tokens_basic():
    assert ai_metrics.estimate_tokens("") == 0
    assert ai_metrics.estimate_tokens("abcd") == 1
    assert ai_metrics.estimate_tokens("abcdefgh") == 2


def test_predict_latency_prefers_exact_bucket(monkeypatch):
    rows_exact = [
        _Row(latency_ms=7000, prompt_tokens_est=520, context_tokens_est=20),
        _Row(latency_ms=7100, prompt_tokens_est=530, context_tokens_est=10),
        _Row(latency_ms=6900, prompt_tokens_est=540, context_tokens_est=0),
        _Row(latency_ms=7300, prompt_tokens_est=500, context_tokens_est=40),
        _Row(latency_ms=7050, prompt_tokens_est=515, context_tokens_est=25),
    ]

    def fake_fetch_recent_rows(**kwargs):
        return rows_exact

    monkeypatch.setattr(ai_metrics, "_fetch_recent_rows", fake_fetch_recent_rows)
    value = ai_metrics.predict_latency_ms(
        provider_name="ollama",
        model_name="qwen:30b",
        feature="ask_ai_text",
        prompt_tokens_est=520,
        context_tokens_est=20,
    )
    assert 6800 <= value <= 7400


def test_predict_latency_fallback_default_on_error(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(ai_metrics, "_fetch_recent_rows", boom)
    value = ai_metrics.predict_latency_ms(
        provider_name="x",
        model_name="y",
        feature="ask_ai_text",
        prompt_tokens_est=100,
        context_tokens_est=20,
    )
    assert value == 3000
