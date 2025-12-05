# План реорганизации структуры проекта

## Новая структура

```
telegram_bot_ai/
├── frontend/              # Telegram бот (frontend)
│   ├── bot.py
│   ├── bot_handlers.py
│   ├── bot_callbacks.py
│   ├── error_handlers.py
│   ├── backend_client.py
│   └── templates/
│       └── buttons.py
│
├── backend/               # Backend API сервис
│   └── backend_service/  # (существующая структура)
│       ├── api/
│       ├── services/
│       ├── models/
│       └── ...
│
├── shared/                # Общие модули (используются и ботом, и backend)
│   ├── rag_system.py
│   ├── document_loaders.py
│   ├── database.py
│   ├── image_processor.py
│   ├── ai_providers.py
│   ├── utils.py
│   ├── web_search.py
│   ├── wiki_scraper.py
│   ├── wiki_git_loader.py
│   ├── ollama_client.py
│   ├── n8n_client.py
│   ├── cache.py
│   ├── logging_config.py
│   ├── config.py
│   └── migrate.py
│
├── tests/                 # Тесты
│   ├── rag_eval.yaml
│   ├── test_rag_quality.py
│   └── run_tests.sh
│
├── docs/                  # Документация
├── data/                  # Данные (логи, БД, кэш)
├── scripts/               # Утилиты и скрипты
│   └── inspect_db.py
│
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── README.md
└── env.template
```

## Файлы для переноса

### Frontend (bot) → `frontend/`
- bot.py
- bot_handlers.py
- bot_callbacks.py
- error_handlers.py
- backend_client.py
- templates/ → frontend/templates/

### Shared → `shared/`
- rag_system.py
- document_loaders.py
- database.py
- image_processor.py
- ai_providers.py
- utils.py
- web_search.py
- wiki_scraper.py
- wiki_git_loader.py
- ollama_client.py
- n8n_client.py
- cache.py
- logging_config.py
- config.py
- migrate.py

### Tests → `tests/`
- tests/rag_eval.yaml
- tests/test_rag_quality.py
- Добавить run_tests.sh

### Scripts → `scripts/`
- inspect_db.py

## Изменения импортов

После переноса нужно обновить импорты:
- `from rag_system import ...` → `from shared.rag_system import ...`
- `from bot_handlers import ...` → `from frontend.bot_handlers import ...`
- И т.д.

## Порядок выполнения

1. Создать новые директории
2. Перенести файлы
3. Обновить импорты
4. Обновить пути в docker-compose.yml и Dockerfile
5. Обновить документацию

