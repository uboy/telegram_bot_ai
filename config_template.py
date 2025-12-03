"""
Шаблон config.py на случай, если вы хотите задать значения вручную
(например, без .env). Для большинства случаев используйте env.template,
скопировав его в .env.
"""

# Telegram Bot Token
TELEGRAM_BOT_TOKEN = "your-telegram-bot-token"

# Database Configuration (используйте только один вариант)
MYSQL_URL = "mysql+mysqlconnector://telegram:telegram@db/telegram_chatbot"
# DB_PATH = "./data/db/bot_database.db"

# Ollama Configuration
OLLAMA_BASE_URL = "http://ollama:11434"
OLLAMA_MODEL = "deepseek-r1:1.5b"
OLLAMA_FILTER_THINKING = True  # Фильтровать thinking tokens из ответов reasoning моделей

# Admin Configuration (список ID через запятую)
ADMIN_IDS = [123456789]

# Redis Configuration
REDIS_HOST = "redis"
REDIS_PORT = 6379

# RAG Configuration
RAG_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
RAG_ENABLE = True
RAG_MAX_CANDIDATES = 100  # Количество кандидатов для векторного поиска перед rerank
RAG_RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"  # Модель reranker'а
RAG_DEVICE = "cpu"  # Устройство: 'cpu', 'cuda' (auto-select GPU), 'cuda:0', 'cuda:1' и т.д.

# RAG Chunking Configuration (по умолчанию из Open WebUI)
RAG_CHUNK_SIZE = 2000  # Размер чанка в символах (~300-800 токенов)
RAG_CHUNK_OVERLAP = 400  # Перекрытие между чанками в символах
RAG_TOP_K = 10  # Количество лучших результатов для контекста
RAG_CONTEXT_LENGTH = 1200  # Максимальная длина контекста на источник в символах
RAG_ENABLE_CITATIONS = True  # Включить inline citations [source_id] в ответах

# Logging (optional)
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5

# n8n Integration
N8N_BASE_URL = "http://n8n:5678"
N8N_DEFAULT_WEBHOOK = "bot-events"
N8N_PUBLIC_URL = "http://localhost:5678"
N8N_API_KEY = ""
