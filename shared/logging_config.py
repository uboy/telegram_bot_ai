"""
Единая конфигурация логирования для бота.

Логи пишутся:
- в консоль (для docker-логов),
- в файл на диске (для просмотра с хоста).

Файл логов по умолчанию: data/logs/bot.log

Параметры ротации настраиваются через переменные окружения:
- LOG_MAX_BYTES - максимальный размер файла в байтах (по умолчанию 10MB)
- LOG_BACKUP_COUNT - количество резервных файлов (по умолчанию 5)
"""

import logging
import os
from logging.handlers import RotatingFileHandler


LOG_DIR = os.getenv("BOT_LOG_DIR", "data/logs")
LOG_FILE = os.path.join(LOG_DIR, "bot.log")

# Параметры ротации из переменных окружения
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", "10485760"))  # 10 MB по умолчанию
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))  # 5 файлов по умолчанию


def setup_logging() -> logging.Logger:
    os.makedirs(LOG_DIR, exist_ok=True)

    # Настроить корневой логгер для всех модулей
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Формат
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Консольный хендлер (docker logs)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Файловый хендлер (ротируемый с настраиваемыми параметрами)
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Отключить избыточное логирование от сторонних библиотек
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)

    # Логгер для бота
    logger = logging.getLogger("bot")
    logger.info(
        "Логирование инициализировано. Файл логов: %s (maxBytes=%s, backupCount=%s)",
        LOG_FILE,
        LOG_MAX_BYTES,
        LOG_BACKUP_COUNT,
    )
    return logger


# Глобальный логгер
logger = setup_logging()


