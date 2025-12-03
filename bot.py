"""
Главный файл Telegram бота-помощника
"""
# Инициализировать логирование ПЕРВЫМ
from logging_config import logger

from telegram.ext import ApplicationBuilder, MessageHandler, CallbackQueryHandler, CommandHandler, filters

from bot_handlers import handle_start, handle_text, handle_document, handle_photo
from bot_callbacks import callback_handler
from error_handlers import global_error_handler
try:
    from config import TELEGRAM_BOT_TOKEN
except ImportError:
    # Fallback если config.py не найден
    import os
    from dotenv import load_dotenv
    load_dotenv()
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN не указан! Создайте .env файл или установите переменную окружения.")
from database import migrate_database


def main():
    """Запуск бота"""
    # Инициализировать базу данных
    logger.info("Инициализация базы данных...")
    migrate_database()
    logger.info("База данных инициализирована")
    
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", handle_start))

    # Обработчики сообщений
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Обработчики callback'ов (кнопки)
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Глобальный обработчик ошибок
    app.add_error_handler(global_error_handler)

    logger.info("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
