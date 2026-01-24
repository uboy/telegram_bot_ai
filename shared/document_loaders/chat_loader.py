"""
Загрузчик для экспорта истории чатов.
Поддерживает Telegram JSON экспорт и простой текстовый формат.
"""
import json
from typing import List, Dict
from datetime import datetime

from .base import DocumentLoader
from .chunking import split_text_structurally


def _parse_telegram_json(data: dict) -> List[Dict[str, str]]:
    messages = data.get("messages") or []
    chunks = []
    buffer = []
    current_day = None

    for msg in messages:
        text = msg.get("text")
        if isinstance(text, list):
            # Telegram export may use list of parts
            text = "".join([t.get("text", "") if isinstance(t, dict) else str(t) for t in text])
        if not text:
            continue

        date_str = msg.get("date") or ""
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            day = dt.date().isoformat()
        except Exception:
            day = "unknown"

        if current_day is None:
            current_day = day
        if day != current_day and buffer:
            content = "\n".join(buffer)
            chunks.append({
                "content": content,
                "title": f"Chat {current_day}",
                "metadata": {"type": "chat", "chat_day": current_day},
            })
            buffer = []
            current_day = day

        sender = msg.get("from") or msg.get("from_id") or "user"
        buffer.append(f"[{sender}] {text}")

    if buffer:
        content = "\n".join(buffer)
        chunks.append({
            "content": content,
            "title": f"Chat {current_day}",
            "metadata": {"type": "chat", "chat_day": current_day},
        })
    return chunks


class ChatLoader(DocumentLoader):
    """Загрузчик для чатов"""

    def load(self, source: str, options: Dict[str, str] | None = None) -> List[Dict[str, str]]:
        try:
            with open(source, "r", encoding="utf-8") as f:
                raw = f.read()
        except Exception:
            with open(source, "r", encoding="latin-1") as f:
                raw = f.read()

        # Try JSON
        try:
            data = json.loads(raw)
            return _parse_telegram_json(data)
        except Exception:
            pass

        # Plain text fallback
        parts = split_text_structurally(raw)
        return [{
            "content": p,
            "title": "Chat export",
            "metadata": {"type": "chat"},
        } for p in parts if p]
