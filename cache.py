"""
Кэширование истории чатов в Redis (опционально).
Если Redis не настроен, функции возвращают None без ошибок.
"""
import logging

logger = logging.getLogger(__name__)

# Условная инициализация Redis
r = None
REDIS_ENABLED = False

try:
    from config import REDIS_ENABLED, REDIS_HOST, REDIS_PORT
    
    if REDIS_ENABLED:
        import redis
        try:
            r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True, socket_connect_timeout=2)
            # Проверка подключения
            r.ping()
            REDIS_ENABLED = True
            logger.info(f"✅ Redis подключен: {REDIS_HOST}:{REDIS_PORT}")
        except Exception as e:
            logger.warning(f"⚠️ Redis не доступен ({REDIS_HOST}:{REDIS_PORT}): {e}. Кэширование отключено.")
            r = None
            REDIS_ENABLED = False
    else:
        logger.info("ℹ️ Redis отключен в конфигурации (REDIS_HOST или REDIS_PORT не установлены)")
except ImportError:
    logger.warning("⚠️ Не удалось импортировать конфигурацию Redis. Кэширование отключено.")
except Exception as e:
    logger.warning(f"⚠️ Ошибка инициализации Redis: {e}. Кэширование отключено.")


def cache_history(chat_id: str, messages: str):
    """Кэшировать историю чата в Redis (если доступен)"""
    if REDIS_ENABLED and r:
        try:
            r.set(f"chat:{chat_id}", messages, ex=600)
        except Exception as e:
            logger.debug(f"Ошибка кэширования в Redis: {e}")


def get_cached_history(chat_id: str):
    """Получить кэшированную историю чата из Redis (если доступен)"""
    if REDIS_ENABLED and r:
        try:
            return r.get(f"chat:{chat_id}")
        except Exception as e:
            logger.debug(f"Ошибка получения из Redis: {e}")
    return None
