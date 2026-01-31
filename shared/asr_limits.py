from __future__ import annotations

import os

DEFAULT_ASR_MAX_FILE_MB = 25

# Telegram Bot API file download hard limit (approx. 20 MB for bots).
TELEGRAM_BOT_FILE_MAX_BYTES = 20 * 1024 * 1024


def get_asr_max_file_mb() -> int:
    value = os.getenv("ASR_MAX_FILE_MB", str(DEFAULT_ASR_MAX_FILE_MB)).strip()
    try:
        parsed = int(value)
        return parsed if parsed > 0 else DEFAULT_ASR_MAX_FILE_MB
    except ValueError:
        return DEFAULT_ASR_MAX_FILE_MB


def get_asr_max_file_bytes() -> int:
    return get_asr_max_file_mb() * 1024 * 1024


def get_telegram_file_max_bytes() -> int:
    return TELEGRAM_BOT_FILE_MAX_BYTES
