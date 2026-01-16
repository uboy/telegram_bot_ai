"""
Утилиты для настроек базы знаний (KB settings).
"""
import json
from typing import Any, Dict


def _default_chunk_size() -> int:
    try:
        from shared.config import RAG_CHUNK_SIZE
        return RAG_CHUNK_SIZE
    except Exception:
        return 1800


def _default_chunk_overlap() -> int:
    try:
        from shared.config import RAG_CHUNK_OVERLAP
        return RAG_CHUNK_OVERLAP
    except Exception:
        return 300


def default_kb_settings() -> Dict[str, Any]:
    chunk_size = _default_chunk_size()
    overlap = _default_chunk_overlap()
    full_max = 200000
    return {
        "chunking": {
            "web": {"mode": "full", "max_chars": full_max, "overlap": 0},
            "wiki": {"mode": "full", "max_chars": full_max, "overlap": 0},
            "markdown": {"mode": "full", "max_chars": full_max, "overlap": 0},
            "text": {"mode": "fixed", "max_chars": chunk_size, "overlap": overlap},
            "code": {"mode": "file", "max_chars": full_max, "overlap": 0},
        },
        "rag": {
            "single_page_mode": True,
            "single_page_top_k": 3,
            "full_page_context_multiplier": 5,
        },
        "ui": {
            "prompt_on_ingest": True,
        },
    }


def _merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def normalize_kb_settings(raw_settings: Any) -> Dict[str, Any]:
    defaults = default_kb_settings()
    if raw_settings is None:
        return defaults
    if isinstance(raw_settings, str):
        try:
            loaded = json.loads(raw_settings)
        except Exception:
            return defaults
        return _merge_dicts(defaults, loaded or {})
    if isinstance(raw_settings, dict):
        return _merge_dicts(defaults, raw_settings)
    return defaults


def dump_kb_settings(settings: Dict[str, Any]) -> str:
    return json.dumps(settings or {}, ensure_ascii=True)


def get_chunking_settings(settings: Dict[str, Any], source_kind: str) -> Dict[str, Any]:
    chunking = (settings or {}).get("chunking") or {}
    return chunking.get(source_kind) or {}

