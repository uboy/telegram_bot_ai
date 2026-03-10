"""
Глобальные обработчики ошибок для Telegram-приложения.

Задачи:
- логировать все неожиданные исключения,
- уведомлять администраторов о критических ошибках.
"""

import traceback
from datetime import datetime, timedelta, timezone
from typing import Dict

from telegram import Update
from telegram.ext import ContextTypes

from shared.config import ADMIN_IDS
from shared.logging_config import logger


# Простейший анти-спам по уведомлениям: не чаще одного раза в N минут
_last_admin_notify: Dict[str, datetime] = {}
_NOTIFY_INTERVAL = timedelta(minutes=5)
_TRANSIENT_ERROR_TYPE_NAMES = {
    "RemoteProtocolError",
    "ReadTimeout",
    "ConnectTimeout",
    "PoolTimeout",
    "ConnectError",
    "ReadError",
    "WriteError",
}
_TRANSIENT_ERROR_SUBSTRINGS = (
    "server disconnected without sending a response",
    "read timed out",
    "connection reset by peer",
    "remote end closed connection without response",
    "connection aborted",
    "broken pipe",
    "temporarily unavailable",
)


def _iter_exception_chain(error: BaseException | None):
    seen: set[int] = set()
    current = error
    while current and id(current) not in seen:
        yield current
        seen.add(id(current))
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)


def _is_transient_transport_error(error: BaseException | None) -> bool:
    for exc in _iter_exception_chain(error):
        if type(exc).__name__ in _TRANSIENT_ERROR_TYPE_NAMES:
            return True
        message = str(exc).strip().lower()
        if any(marker in message for marker in _TRANSIENT_ERROR_SUBSTRINGS):
            return True
    return False


async def notify_admins(message: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправить уведомление всем администраторам (с простым ограничением по частоте)."""
    now = datetime.now(timezone.utc)
    key = "global_error"
    last = _last_admin_notify.get(key)
    if last and now - last < _NOTIFY_INTERVAL:
        return

    _last_admin_notify[key] = now

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=message)
        except Exception as e:
            logger.warning("Не удалось отправить уведомление админу %s: %s", admin_id, e)


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Глобальный обработчик ошибок Telegram.

    Логирует исключение и отправляет краткое уведомление администраторам.
    """
    error = context.error
    tb_str = "".join(
        traceback.format_exception(
            type(error),
            error,
            error.__traceback__,
        )
    )
    if _is_transient_transport_error(error):
        logger.warning("Подавлено transient-уведомление Telegram transport error: %s", error)
        logger.warning("Transient traceback:\n%s", tb_str)
        return

    logger.error("Исключение в обработчике Telegram: %s", error)
    logger.error("Traceback:\n%s", tb_str)

    # Сформировать краткое сообщение для админов
    update_info = ""
    if isinstance(update, Update):
        if update.effective_user:
            update_info += f"Пользователь: {update.effective_user.id}\n"
        if update.effective_chat:
            update_info += f"Чат: {update.effective_chat.id}\n"
        if update.callback_query:
            update_info += "Тип: callback_query\n"
        elif update.message:
            update_info += "Тип: message\n"

    text = (
        "⚠️ В боте произошла критическая ошибка.\n\n"
        f"{update_info}"
        f"Ошибка: {type(error).__name__}: {error}"
    )

    await notify_admins(text, context)


