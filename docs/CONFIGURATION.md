# Конфигурация

## Базовый порядок

1. Скопируйте шаблон:
```bash
Copy-Item env.template .env
```

2. Заполните обязательные значения:
- `TELEGRAM_BOT_TOKEN`
- `ADMIN_IDS`
- `MYSQL_URL` или `DB_PATH`
  - Для docker launcher (`scripts/start_stack.py`) `MYSQL_URL` используется как переключатель профиля MySQL.

3. Для защищённого режима задайте:
- `BACKEND_API_KEY`

## Основные переменные

### Backend/bot
- `BACKEND_BASE_URL`
- `BACKEND_API_PREFIX` (обычно `/api/v1`)
- `BACKEND_API_KEY`
- `AI_DEFAULT_PROVIDER`

### RAG
- `RAG_ENABLE`
- `RAG_MODEL_NAME`
- `RAG_RERANK_MODEL`
- `RAG_BACKEND` (`legacy` | `qdrant`)
- `RAG_TOP_K`
- `RAG_CONTEXT_LENGTH`
- `RAG_CHUNK_SIZE`
- `RAG_CHUNK_OVERLAP`
- `QDRANT_URL`
- `QDRANT_API_KEY` (optional)
- `QDRANT_COLLECTION`
- `QDRANT_TIMEOUT_SEC`
- `HF_TOKEN` (опционально, для gated/private моделей Hugging Face и лимитов API)

### RAG index outbox worker
- `RAG_INDEX_OUTBOX_WORKER_ENABLED`
- `RAG_INDEX_OUTBOX_POLL_INTERVAL_SEC`
- `RAG_INDEX_OUTBOX_BATCH_SIZE`
- `RAG_INDEX_OUTBOX_MAX_ATTEMPTS`
- `RAG_INDEX_OUTBOX_RETRY_BASE_SEC`
- `RAG_INDEX_OUTBOX_RETRY_MAX_SEC`
- `RAG_INDEX_DRIFT_AUDIT_INTERVAL_SEC`
- `RAG_INDEX_DRIFT_MAX_KBS`
- `RAG_INDEX_DRIFT_WARNING_RATIO`
- `RAG_INDEX_DRIFT_CRITICAL_RATIO`

### RAG retention lifecycle
- `RAG_RETENTION_ENABLED`
- `RAG_RETENTION_INTERVAL_SEC`
- `RAG_RETENTION_QUERY_LOG_DAYS`
- `RAG_RETENTION_DOC_OLD_VERSION_DAYS`
- `RAG_RETENTION_EVAL_DAYS`
- `RAG_RETENTION_DRIFT_AUDIT_DAYS`
- `RAG_RETENTION_AUDIT_DAYS`

### RAG eval orchestration
- `RAG_EVAL_DEFAULT_SLICES`
- `RAG_EVAL_SUITE_FILE`
- `RAG_EVAL_THRESHOLD_RECALL_AT10`
- `RAG_EVAL_THRESHOLD_MRR_AT10`
- `RAG_EVAL_THRESHOLD_NDCG_AT10`

### AI providers
- Ollama:
  - `OLLAMA_BASE_URL`
  - `OLLAMA_MODEL`
  - `OLLAMA_VISION_MODEL`
- OpenAI:
  - `OPENAI_API_KEY`
  - `OPENAI_MODEL`
  - `OPENAI_BASE_URL` (опционально, для OpenAI-compatible endpoint)
- Anthropic:
  - `ANTHROPIC_API_KEY`
  - `ANTHROPIC_MODEL`
  - `ANTHROPIC_BASE_URL` (опционально)
- DeepSeek:
  - `DEEPSEEK_API_KEY`
  - `DEEPSEEK_MODEL`
  - `DEEPSEEK_BASE_URL` (опционально)
- Open WebUI:
  - `OPEN_WEBUI_BASE_URL`
  - `OPEN_WEBUI_API_KEY`
  - `OPEN_WEBUI_MODEL`

### Direct AI mode v2
- `AI_CONTEXT_RESTORE_TTL_HOURS` — окно (часы), в котором предлагается восстановление предыдущего диалога.
- `AI_CONTEXT_RECENT_TURNS` — сколько последних реплик хранить verbatim в контексте.
- `AI_CONTEXT_BUDGET_TOKENS_DEFAULT` — базовый лимит токенов контекста для сжатия.
- `AI_CONTEXT_BUDGETS_JSON` — JSON-override лимитов по моделям, пример:
  - `{"qwen:30b": 1400, "llama3.1:70b": 2000}`
- `AI_PROGRESS_THRESHOLD_SEC` — порог показа временного progress-сообщения для долгих AI-запросов.
- `AI_FIRST_REPLY_MAX_WORDS` — лимит краткости первого ответа в direct AI mode.

### ASR
- `ASR_MAX_FILE_MB`
- `ASR_QUEUE_MAX`
- `ASR_MAX_WORKERS`

### Analytics
- `ANALYTICS_ENABLED`
- `ANALYTICS_MIN_TEXT_LENGTH`
- `ANALYTICS_EMBEDDING_BATCH_SIZE`
- `ANALYTICS_MAX_THEMES`
- `ANALYTICS_CLUSTER_METHOD`
- `ANALYTICS_CLUSTER_MIN_SIZE`
- `ANALYTICS_DIGEST_MAX_MESSAGES`
- `ANALYTICS_RETENTION_DAYS`

### Optional integrations
- `N8N_BASE_URL`
- `N8N_DEFAULT_WEBHOOK`
- `N8N_PUBLIC_URL`
- `N8N_API_KEY`

## Безопасность

- `.env` должен оставаться только локальным.
- Не коммитьте реальные токены/пароли.
- Для production всегда задавайте `BACKEND_API_KEY`.
- Перед завершением задач запускайте `python scripts/scan_secrets.py`.
