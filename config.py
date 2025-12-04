"""
Конфигурация бота из переменных окружения
"""
import os
from dotenv import load_dotenv

# Загрузить переменные окружения из .env файла (если он существует)
# load_dotenv() не выдает ошибку, если файл не найден
try:
    load_dotenv()
except Exception as e:
    # Если есть проблема с dotenv, продолжаем работу с переменными окружения системы
    import warnings
    warnings.warn(f"Не удалось загрузить .env файл: {e}. Используются переменные окружения системы.")

# Telegram Bot Token
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Database Configuration
# Если переменная не установлена или пустая, считаем что MySQL отключен
MYSQL_URL = os.getenv("MYSQL_URL", None) or None
if MYSQL_URL and not MYSQL_URL.strip():
    MYSQL_URL = None
DB_PATH = os.getenv("DB_PATH", None) or None
if DB_PATH and not DB_PATH.strip():
    DB_PATH = None

# Ollama Configuration
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "deepseek-r1:1.5b")
# Фильтровать thinking tokens из ответов reasoning моделей (deepseek-r1, qwen3 и т.д.)
OLLAMA_FILTER_THINKING = os.getenv("OLLAMA_FILTER_THINKING", "true").lower() == "true"

# Admin Configuration
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
if ADMIN_IDS_STR:
    # Поддержка как списка через запятую, так и одного ID
    ADMIN_IDS = [int(id.strip()) for id in ADMIN_IDS_STR.split(",") if id.strip()]
else:
    ADMIN_IDS = []

# Redis Configuration
# Если переменные не установлены или пустые, считаем что Redis отключен
REDIS_ENABLED = False
REDIS_HOST = os.getenv("REDIS_HOST", "").strip()
REDIS_PORT_STR = os.getenv("REDIS_PORT", "").strip()
if REDIS_HOST and REDIS_PORT_STR:
    try:
        REDIS_PORT = int(REDIS_PORT_STR)
        REDIS_ENABLED = True
    except ValueError:
        REDIS_PORT = 6379
        REDIS_ENABLED = False
else:
    REDIS_HOST = "localhost"
    REDIS_PORT = 6379
    REDIS_ENABLED = False

# RAG Configuration (optional)
# Оптимальные дефолты для RU+EN под GPU (V100 32GB): мультиязычные эмбеддинги + reranker.
RAG_MODEL_NAME = os.getenv("RAG_MODEL_NAME", "intfloat/multilingual-e5-base")
RAG_ENABLE = os.getenv("RAG_ENABLE", "true").lower() == "true"
RAG_MAX_CANDIDATES = int(os.getenv("RAG_MAX_CANDIDATES", "80"))  # Количество кандидатов для векторного поиска перед rerank
RAG_RERANK_MODEL = os.getenv("RAG_RERANK_MODEL", "BAAI/bge-reranker-base")  # Мультиязычный reranker
# Устройство для моделей RAG: 'cpu', 'cuda' (автоматически выберет GPU), 'cuda:0', 'cuda:1' и т.д.
RAG_DEVICE = os.getenv("RAG_DEVICE", "cuda")

# RAG Chunking Configuration (по умолчанию из Open WebUI, адаптировано)
RAG_CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "1800"))  # Размер чанка в символах (~400–800 токенов)
RAG_CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "300"))  # Перекрытие между чанками в символах
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "12"))  # Количество лучших результатов для контекста
RAG_CONTEXT_LENGTH = int(os.getenv("RAG_CONTEXT_LENGTH", "1500"))  # Максимальная длина контекста на источник в символах
RAG_ENABLE_CITATIONS = os.getenv("RAG_ENABLE_CITATIONS", "true").lower() == "true"  # Включить inline citations
RAG_MIN_RERANK_SCORE = float(os.getenv("RAG_MIN_RERANK_SCORE", "0.15"))  # Порог уверенности для ответа (если есть reranker)

# n8n Integration
# Если N8N_BASE_URL не установлен или пустой, считаем что n8n отключен
N8N_BASE_URL_RAW = os.getenv("N8N_BASE_URL", "").strip()
if N8N_BASE_URL_RAW:
    N8N_BASE_URL = N8N_BASE_URL_RAW.rstrip("/")
    N8N_ENABLED = True
else:
    N8N_BASE_URL = ""
    N8N_ENABLED = False

N8N_API_KEY = os.getenv("N8N_API_KEY", "").strip()
N8N_DEFAULT_WEBHOOK = os.getenv("N8N_DEFAULT_WEBHOOK", "bot-events").strip("/") if N8N_ENABLED else ""
N8N_PUBLIC_URL = os.getenv("N8N_PUBLIC_URL", "http://localhost:5678").rstrip("/") if N8N_ENABLED else ""
try:
    N8N_TIMEOUT = int(os.getenv("N8N_TIMEOUT", "10"))
except ValueError:
    N8N_TIMEOUT = 10

# Проверка обязательных параметров
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не указан в .env файле!")

if not ADMIN_IDS:
    print("⚠️ ВНИМАНИЕ: ADMIN_IDS не указан в .env файле! Бот не сможет одобрять пользователей.")
