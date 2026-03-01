# Архитектура: Telegram Bot + FastAPI Backend

## 1. Контур системы

- **Frontend bot**: `frontend/bot.py`, `frontend/bot_handlers.py`, `frontend/bot_callbacks.py`
- **Backend API**: `backend/app.py`, `backend/api/routes/*`, `backend/services/*`
- **Shared layer**: `shared/*` (RAG, DB модели, провайдеры ИИ, утилиты, безопасность)

Ключевой принцип: бот работает как клиент backend API и не выполняет бизнес-логику ingestion/RAG напрямую.

## 2. Основные API-группы

- `/api/v1/health`
- `/api/v1/auth/*`, `/api/v1/users/*`
- `/api/v1/knowledge-bases/*`
- `/api/v1/ingestion/*`
- `/api/v1/jobs/*`
- `/api/v1/rag/*`
- `/api/v1/asr/*`
- `/api/v1/analytics/*`

## 3. Потоки данных

### 3.1 KB ingestion
1. Админ запускает загрузку из бота (документ/web/wiki/image/code).
2. Бот вызывает `/ingestion/*`.
3. Backend создаёт async job (`Job`) и запускает обработку в фоне.
4. Клиент получает статус через `/jobs/{job_id}`.
5. Чанки сохраняются в SQL + индексируются в FAISS.

### 3.2 RAG query / summary
1. Пользователь отправляет запрос в боте.
2. Бот вызывает `/rag/query` или `/rag/summary`.
3. Backend делает retrieval, формирует контекст, вызывает LLM.
4. Safety-фильтры очищают ответ (цитаты/URL/команды).
5. Бот рендерит ответ с источниками.

### 3.3 ASR flow
1. Бот получает voice/audio и отправляет в `/asr/transcribe`.
2. Backend ставит задачу в глобальную очередь ASR.
3. Worker обрабатывает файл и обновляет статус.
4. Бот опрашивает `/asr/jobs/{job_id}` и отправляет транскрипцию.

### 3.4 Chat analytics
1. Бот публикует сообщения групп в `/analytics/messages`.
2. Backend сохраняет данные и строит поисковые/дайджест-пайплайны.
3. Дайджесты/поиск/QA доступны через `/analytics/*`.

## 4. Безопасность

- API key header: `X-API-Key` для всех protected endpoint-групп.
- Public only:
  - `GET /api/v1/health`
  - `POST /api/v1/auth/telegram`
- Секреты читаются из `.env`; `.env` не должен попадать в git.

## 5. Хранилища

- SQL DB (MySQL/SQLite): пользователи, KB, чанки, логи, job-статусы, analytics сущности.
- FAISS: векторные индексы.
- Redis: опционально для runtime integration.

## 6. Наблюдаемость

- Единый logger в `shared/logging_config.py`.
- Точки контроля:
  - ingestion job status
  - ASR queue/job status
  - analytics digest/import status

## 7. Текущие ограничения

- Фоновые задачи реализованы in-process thread model (не распределённая очередь).
- Часть e2e-проверок ещё выполняется вручную (см. `docs/REQUIREMENTS_TRACEABILITY.md`).
