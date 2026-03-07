"""
Конфигурация бота из переменных окружения
"""
import os
import json
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

# Hugging Face token (optional): allows access to gated/private models and higher rate limits.
HF_TOKEN = (
    os.getenv("HF_TOKEN", "").strip()
    or os.getenv("HUGGINGFACE_HUB_TOKEN", "").strip()
)
if HF_TOKEN:
    # Normalize aliases expected by different HF libraries.
    os.environ["HF_TOKEN"] = HF_TOKEN
    os.environ["HUGGINGFACE_HUB_TOKEN"] = HF_TOKEN

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
try:
    RAG_MAX_CANDIDATES = max(1, int(os.getenv("RAG_MAX_CANDIDATES", "150")))
except ValueError:
    RAG_MAX_CANDIDATES = 150
try:
    RAG_DENSE_CANDIDATES = max(1, int(os.getenv("RAG_DENSE_CANDIDATES", str(RAG_MAX_CANDIDATES))))
except ValueError:
    RAG_DENSE_CANDIDATES = RAG_MAX_CANDIDATES
try:
    RAG_BM25_CANDIDATES = max(1, int(os.getenv("RAG_BM25_CANDIDATES", str(RAG_MAX_CANDIDATES))))
except ValueError:
    RAG_BM25_CANDIDATES = RAG_MAX_CANDIDATES
try:
    RAG_RERANK_TOP_N = max(
        1,
        int(
            os.getenv(
                "RAG_RERANK_TOP_N",
                str(max(RAG_DENSE_CANDIDATES, RAG_BM25_CANDIDATES)),
            )
        ),
    )
except ValueError:
    RAG_RERANK_TOP_N = max(RAG_DENSE_CANDIDATES, RAG_BM25_CANDIDATES)
RAG_RERANK_MODEL = os.getenv("RAG_RERANK_MODEL", "BAAI/bge-reranker-base")
RAG_ENABLE_RERANK = os.getenv("RAG_ENABLE_RERANK", "true").lower() == "true"  # Мультиязычный reranker
# Устройство для моделей RAG: 'cpu', 'cuda' (автоматически выберет GPU), 'cuda:0', 'cuda:1' и т.д.
RAG_DEVICE = os.getenv("RAG_DEVICE", "cuda")

# RAG Chunking Configuration (по умолчанию из Open WebUI, адаптировано)
RAG_CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "1800"))  # Размер чанка в символах (~400–800 токенов)
RAG_CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "300"))  # Перекрытие между чанками в символах
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "8"))  # Количество лучших результатов для контекста
RAG_CONTEXT_LENGTH = int(os.getenv("RAG_CONTEXT_LENGTH", "2500"))  # Максимальная длина контекста на источник в символах
RAG_ENABLE_CITATIONS = os.getenv("RAG_ENABLE_CITATIONS", "true").lower() == "true"  # Включить inline citations
RAG_MIN_RERANK_SCORE = float(os.getenv("RAG_MIN_RERANK_SCORE", "0.15"))  # Порог уверенности для ответа (если есть reranker)
RAG_DEBUG_RETURN_CHUNKS = os.getenv("RAG_DEBUG_RETURN_CHUNKS", "false").lower() == "true"  # Возвращать debug информацию о чанках

# RAG v3 backend switch
# legacy -> in-process FAISS/BM25
# qdrant -> dense retrieval via external Qdrant + local lexical channel
RAG_BACKEND = os.getenv("RAG_BACKEND", "legacy").strip().lower()
if RAG_BACKEND not in {"legacy", "qdrant"}:
    RAG_BACKEND = "legacy"

# Orchestrator switch (Phase D cutover)
# false -> legacy route-level intent boosts/fallback ranking
# true  -> v4 primary path without query-specific hardcoded boosts
RAG_ORCHESTRATOR_V4 = os.getenv("RAG_ORCHESTRATOR_V4", "false").lower() == "true"

# Legacy query heuristics switch (generalization hardening)
# false -> legacy route works in generalized mode (no route-level query-specific boosts/fallback)
# true  -> enable legacy query-intent boosts/fallback behavior (temporary rollback switch)
RAG_LEGACY_QUERY_HEURISTICS = os.getenv("RAG_LEGACY_QUERY_HEURISTICS", "false").lower() == "true"

QDRANT_URL = os.getenv("QDRANT_URL", "").strip().rstrip("/")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip()
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "rag_chunks_v3").strip()
try:
    QDRANT_TIMEOUT_SEC = float(os.getenv("QDRANT_TIMEOUT_SEC", "10"))
except ValueError:
    QDRANT_TIMEOUT_SEC = 10.0

# RAG index outbox worker (Phase B)
RAG_INDEX_OUTBOX_WORKER_ENABLED = os.getenv("RAG_INDEX_OUTBOX_WORKER_ENABLED", "true").lower() == "true"
try:
    RAG_INDEX_OUTBOX_POLL_INTERVAL_SEC = float(os.getenv("RAG_INDEX_OUTBOX_POLL_INTERVAL_SEC", "2"))
except ValueError:
    RAG_INDEX_OUTBOX_POLL_INTERVAL_SEC = 2.0
try:
    RAG_INDEX_OUTBOX_BATCH_SIZE = int(os.getenv("RAG_INDEX_OUTBOX_BATCH_SIZE", "50"))
except ValueError:
    RAG_INDEX_OUTBOX_BATCH_SIZE = 50
try:
    RAG_INDEX_OUTBOX_MAX_ATTEMPTS = int(os.getenv("RAG_INDEX_OUTBOX_MAX_ATTEMPTS", "6"))
except ValueError:
    RAG_INDEX_OUTBOX_MAX_ATTEMPTS = 6
try:
    RAG_INDEX_OUTBOX_RETRY_BASE_SEC = int(os.getenv("RAG_INDEX_OUTBOX_RETRY_BASE_SEC", "5"))
except ValueError:
    RAG_INDEX_OUTBOX_RETRY_BASE_SEC = 5
try:
    RAG_INDEX_OUTBOX_RETRY_MAX_SEC = int(os.getenv("RAG_INDEX_OUTBOX_RETRY_MAX_SEC", "300"))
except ValueError:
    RAG_INDEX_OUTBOX_RETRY_MAX_SEC = 300
try:
    RAG_INDEX_DRIFT_AUDIT_INTERVAL_SEC = float(os.getenv("RAG_INDEX_DRIFT_AUDIT_INTERVAL_SEC", "300"))
except ValueError:
    RAG_INDEX_DRIFT_AUDIT_INTERVAL_SEC = 300.0
try:
    RAG_INDEX_DRIFT_MAX_KBS = int(os.getenv("RAG_INDEX_DRIFT_MAX_KBS", "200"))
except ValueError:
    RAG_INDEX_DRIFT_MAX_KBS = 200
try:
    RAG_INDEX_DRIFT_WARNING_RATIO = float(os.getenv("RAG_INDEX_DRIFT_WARNING_RATIO", "0.0005"))
except ValueError:
    RAG_INDEX_DRIFT_WARNING_RATIO = 0.0005
try:
    RAG_INDEX_DRIFT_CRITICAL_RATIO = float(os.getenv("RAG_INDEX_DRIFT_CRITICAL_RATIO", "0.001"))
except ValueError:
    RAG_INDEX_DRIFT_CRITICAL_RATIO = 0.001

# Retention lifecycle worker
RAG_RETENTION_ENABLED = os.getenv("RAG_RETENTION_ENABLED", "true").lower() == "true"
try:
    RAG_RETENTION_INTERVAL_SEC = float(os.getenv("RAG_RETENTION_INTERVAL_SEC", "3600"))
except ValueError:
    RAG_RETENTION_INTERVAL_SEC = 3600.0
try:
    RAG_RETENTION_QUERY_LOG_DAYS = int(os.getenv("RAG_RETENTION_QUERY_LOG_DAYS", "30"))
except ValueError:
    RAG_RETENTION_QUERY_LOG_DAYS = 30
try:
    RAG_RETENTION_DOC_OLD_VERSION_DAYS = int(os.getenv("RAG_RETENTION_DOC_OLD_VERSION_DAYS", "30"))
except ValueError:
    RAG_RETENTION_DOC_OLD_VERSION_DAYS = 30
try:
    RAG_RETENTION_EVAL_DAYS = int(os.getenv("RAG_RETENTION_EVAL_DAYS", "90"))
except ValueError:
    RAG_RETENTION_EVAL_DAYS = 90
try:
    RAG_RETENTION_DRIFT_AUDIT_DAYS = int(os.getenv("RAG_RETENTION_DRIFT_AUDIT_DAYS", "90"))
except ValueError:
    RAG_RETENTION_DRIFT_AUDIT_DAYS = 90
try:
    RAG_RETENTION_AUDIT_DAYS = int(os.getenv("RAG_RETENTION_AUDIT_DAYS", "365"))
except ValueError:
    RAG_RETENTION_AUDIT_DAYS = 365

# RAG eval orchestration thresholds
RAG_EVAL_DEFAULT_SLICES = os.getenv("RAG_EVAL_DEFAULT_SLICES", "")
RAG_EVAL_SUITE_FILE = os.getenv("RAG_EVAL_SUITE_FILE", "")
try:
    RAG_EVAL_THRESHOLD_RECALL_AT10 = float(os.getenv("RAG_EVAL_THRESHOLD_RECALL_AT10", "0.6"))
except ValueError:
    RAG_EVAL_THRESHOLD_RECALL_AT10 = 0.6
try:
    RAG_EVAL_THRESHOLD_MRR_AT10 = float(os.getenv("RAG_EVAL_THRESHOLD_MRR_AT10", "0.45"))
except ValueError:
    RAG_EVAL_THRESHOLD_MRR_AT10 = 0.45
try:
    RAG_EVAL_THRESHOLD_NDCG_AT10 = float(os.getenv("RAG_EVAL_THRESHOLD_NDCG_AT10", "0.5"))
except ValueError:
    RAG_EVAL_THRESHOLD_NDCG_AT10 = 0.5

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

# Chat Analytics
ANALYTICS_ENABLED = os.getenv('ANALYTICS_ENABLED', 'true').lower() == 'true'
ANALYTICS_MIN_TEXT_LENGTH = int(os.getenv('ANALYTICS_MIN_TEXT_LENGTH', '10'))
ANALYTICS_EMBEDDING_BATCH_SIZE = int(os.getenv('ANALYTICS_EMBEDDING_BATCH_SIZE', '64'))
ANALYTICS_MAX_THEMES = int(os.getenv('ANALYTICS_MAX_THEMES', '15'))
ANALYTICS_CLUSTER_METHOD = os.getenv('ANALYTICS_CLUSTER_METHOD', 'hdbscan')
ANALYTICS_CLUSTER_MIN_SIZE = int(os.getenv('ANALYTICS_CLUSTER_MIN_SIZE', '5'))
ANALYTICS_DIGEST_MAX_MESSAGES = int(os.getenv('ANALYTICS_DIGEST_MAX_MESSAGES', '10000'))
ANALYTICS_RETENTION_DAYS = int(os.getenv('ANALYTICS_RETENTION_DAYS', '365'))

# Direct AI mode v2 (session memory + progress + concise-first policy)
AI_CONTEXT_RESTORE_TTL_HOURS = int(os.getenv("AI_CONTEXT_RESTORE_TTL_HOURS", "24"))
AI_CONTEXT_RECENT_TURNS = int(os.getenv("AI_CONTEXT_RECENT_TURNS", "6"))
AI_CONTEXT_BUDGET_TOKENS_DEFAULT = int(os.getenv("AI_CONTEXT_BUDGET_TOKENS_DEFAULT", "1800"))
AI_PROGRESS_THRESHOLD_SEC = float(os.getenv("AI_PROGRESS_THRESHOLD_SEC", "5"))
AI_FIRST_REPLY_MAX_WORDS = int(os.getenv("AI_FIRST_REPLY_MAX_WORDS", "120"))

_ai_context_budgets_raw = os.getenv("AI_CONTEXT_BUDGETS_JSON", "").strip()
AI_CONTEXT_BUDGETS = {}
if _ai_context_budgets_raw:
    try:
        parsed = json.loads(_ai_context_budgets_raw)
        if isinstance(parsed, dict):
            AI_CONTEXT_BUDGETS = {
                str(k): int(v)
                for k, v in parsed.items()
                if str(k).strip() and str(v).strip()
            }
    except Exception:
        AI_CONTEXT_BUDGETS = {}

# Проверка обязательных параметров
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не указан в .env файле!")

if not ADMIN_IDS:
    print("⚠️ ВНИМАНИЕ: ADMIN_IDS не указан в .env файле! Бот не сможет одобрять пользователей.")
