import importlib
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _reload_config_module():
    if "shared.config" in sys.modules:
        return importlib.reload(sys.modules["shared.config"])
    return importlib.import_module("shared.config")


def test_hf_token_loaded_from_hf_token_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ADMIN_IDS", "1")
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    monkeypatch.delenv("HUGGINGFACE_HUB_TOKEN", raising=False)

    config = _reload_config_module()

    assert config.HF_TOKEN == "hf_test_token"
    assert os.getenv("HF_TOKEN") == "hf_test_token"
    assert os.getenv("HUGGINGFACE_HUB_TOKEN") == "hf_test_token"


def test_hf_token_loaded_from_huggingface_hub_token_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ADMIN_IDS", "1")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("HUGGINGFACE_HUB_TOKEN", "hf_hub_token")

    config = _reload_config_module()

    assert config.HF_TOKEN == "hf_hub_token"
    assert os.getenv("HF_TOKEN") == "hf_hub_token"
    assert os.getenv("HUGGINGFACE_HUB_TOKEN") == "hf_hub_token"
