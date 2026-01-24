import json
from shared.document_loaders.chat_loader import ChatLoader


def test_chat_loader_telegram_json(tmp_path):
    data = {
        "messages": [
            {"date": "2024-01-01T10:00:00", "from": "A", "text": "Hello"},
            {"date": "2024-01-01T10:01:00", "from": "B", "text": "Hi"},
        ]
    }
    path = tmp_path / "chat.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    loader = ChatLoader()
    chunks = loader.load(str(path))
    assert chunks
    assert "Hello" in chunks[0]["content"]
