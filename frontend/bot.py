"""
Главный файл Telegram бота-помощника
"""
# Инициализировать логирование ПЕРВЫМ
from shared.logging_config import logger
from shared.networking import build_telegram_request, log_proxy_configuration

from telegram.ext import ApplicationBuilder, MessageHandler, CallbackQueryHandler, CommandHandler, filters

from frontend.bot_handlers import handle_start, handle_text, handle_document, handle_photo, handle_voice, handle_audio, message_collector
from frontend.bot_callbacks import callback_handler
from frontend.error_handlers import global_error_handler
try:
    from shared.config import TELEGRAM_BOT_TOKEN
except ImportError:
    # Fallback если config.py не найден
    import os
    from dotenv import load_dotenv
    load_dotenv()
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN не указан! Создайте .env файл или установите переменную окружения.")
from shared.database import migrate_database


def build_application(token: str):
    request = build_telegram_request()
    builder = ApplicationBuilder().token(token).request(request).get_updates_request(request)
    return builder.build()


def main():
    """Запуск бота"""
    # Инициализировать базу данных
    logger.info("Инициализация базы данных...")
    migrate_database()
    logger.info("База данных инициализирована")
    log_proxy_configuration(logger, "telegram_bot")

    app = build_application(TELEGRAM_BOT_TOKEN)

    # Команды
    app.add_handler(CommandHandler("start", handle_start))

    # Обработчики сообщений
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    # Обработчики callback'ов (кнопки)
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Chat analytics: collect group messages (group=10 = runs after normal handlers)
    app.add_handler(
        MessageHandler(
            filters.TEXT & (filters.ChatType.SUPERGROUP | filters.ChatType.GROUP),
            message_collector,
        ),
        group=10,
    )

    # Глобальный обработчик ошибок
    app.add_error_handler(global_error_handler)

    logger.info("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()

