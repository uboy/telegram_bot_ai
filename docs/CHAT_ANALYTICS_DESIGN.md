# Технический дизайн: Аналитика чатов, извлечение тем и дайджесты для Telegram

**Статус:** Draft
**Автор:** Architect Agent
**Дата:** 2026-02-10
**Входные данные:** ТЗ на аналитику чатов, текущая кодовая база проекта

---

## Оглавление

1. [Высокоуровневая архитектура](#1-высокоуровневая-архитектура)
2. [Модель данных](#2-модель-данных)
3. [Основные компоненты и зоны ответственности](#3-основные-компоненты-и-зоны-ответственности)
4. [Потоки данных](#4-потоки-данных)
5. [API / границы модулей](#5-api--границы-модулей)
6. [Bot UI flow](#6-bot-ui-flow)
7. [Стратегия сбора сообщений](#7-стратегия-сбора-сообщений)
8. [Подход к кластеризации и тематизации](#8-подход-к-кластеризации-и-тематизации)
9. [Система расписаний](#9-система-расписаний)
10. [Интеграция с существующим RAG](#10-интеграция-с-существующим-rag)
11. [Риски и митигации](#11-риски-и-митигации)
12. [План имплементации](#12-план-имплементации)

---

## 1. Высокоуровневая архитектура

### Текущая архитектура (as-is)

```
Telegram User
    |
    v
[Frontend Bot]  --httpx-->  [FastAPI Backend]  -->  [shared/]
  frontend/                    backend/               rag_system.py
  bot_handlers.py              api/routes/            database.py
  bot_callbacks.py             services/              ai_providers.py
  backend_client.py                                   document_loaders/
    |                              |
    v                              v
[python-telegram-bot 22.5]     [MySQL 8 / SQLite]
                               [Redis 7]
                               [FAISS in-memory]
```

Ключевой принцип: **бот не трогает БД и RAG напрямую** -- все через backend_client.py -> FastAPI.

### Целевая архитектура (to-be)

```
Telegram Supergroup/Topic
    |  (все сообщения)
    v
[Frontend Bot]  --(1) collect -->  [FastAPI Backend]
  + MessageCollectorHandler         + /api/v1/analytics/*
  + AnalyticsCommandHandler         + ChatAnalyticsService
  + DigestCallbackHandler           + ThemeClusteringService
    |                               + DigestGeneratorService
    |  httpx                        + ChatSearchService
    v                               + SchedulerService
[backend_client.py]                 + HistoryImportService
  + chat_analytics_* methods            |
                                        v
                                   [shared/]
                                     + chat_analytics_rag.py  (FAISS for messages)
                                     + database.py  (+ new models)
                                     + ai_providers.py  (LLM for summaries)
                                        |
                                        v
                                   [MySQL/SQLite]  [Redis]  [FAISS]

                                   [APScheduler]  (in-process, backend)
```

### Архитектурные решения

**ADR-1: Новая функциональность живет внутри существующих сервисов (bot + backend), а не как отдельный микросервис.**

- Контекст: У нас 2 сервиса (bot, backend) с общим shared/. Добавление третьего сервиса увеличит сложность деплоя для малой команды.
- Решение: Новые модули добавляются в backend/services/ и frontend/. Общие модели -- в shared/database.py.
- Последствия: Простота деплоя, но backend становится "толще". При необходимости отдельный worker можно выделить позже.

**ADR-2: Используем APScheduler внутри backend для cron-задач, не Celery.**

- Контекст: Redis уже есть, но Celery требует отдельного worker-процесса и значительно усложняет стек. Текущий backend однопроцессный (uvicorn --workers 1).
- Решение: APScheduler (AsyncIOScheduler) запускается при старте backend, хранит задачи в БД (JobStore=SQLAlchemy).
- Последствия: Простая интеграция, но не масштабируется горизонтально. Для MVP -- достаточно.

**ADR-3: Отдельный FAISS-индекс для сообщений чатов, не общий с knowledge_chunks.**

- Контекст: knowledge_chunks хранит документы из баз знаний. Сообщения чатов -- принципиально другой тип данных с другим lifecycle.
- Решение: Новая таблица `chat_message_embeddings` + отдельный FAISS index per chat в `ChatAnalyticsRAG`.
- Последствия: Изоляция данных, независимый rebuild индекса, но дублирование embedding-инфраструктуры.

---

## 2. Модель данных

### Новые таблицы

```
Существующие:
  users (id, telegram_id, username, full_name, role, approved, ...)
  messages (id, chat_id, user, text, timestamp)  <-- простой кеш, недостаточен

Новые:
  chat_messages              -- Полная история сообщений
  chat_analytics_configs     -- Настройки аналитики per-chat
  chat_digests               -- Сгенерированные дайджесты
  chat_digest_themes         -- Темы внутри дайджеста
  chat_message_embeddings    -- Кеш эмбеддингов сообщений
  chat_import_logs           -- Лог импортов истории
```

### Схема таблиц (SQLAlchemy models для shared/database.py)

```python
class ChatMessage(Base):
    """Полная история сообщений из Telegram supergroup topics"""
    __tablename__ = 'chat_messages'

    id = Column(Integer, primary_key=True)
    # Идентификация сообщения
    chat_id = Column(String(20), nullable=False, index=True)       # Telegram chat_id
    thread_id = Column(Integer, nullable=True, index=True)          # message_thread_id (topic)
    message_id = Column(Integer, nullable=False)                    # Telegram message_id

    # Автор
    author_telegram_id = Column(String(20), nullable=True)          # telegram user id
    author_username = Column(String(100), nullable=True)            # @username
    author_display_name = Column(String(200), nullable=True)        # first_name + last_name

    # Содержимое
    text = Column(Text, nullable=True)                              # Текст сообщения
    message_link = Column(String(500), nullable=True)               # t.me/c/chatid/msgid

    # Метаданные
    timestamp = Column(DateTime, nullable=False, index=True)
    is_bot_message = Column(Boolean, default=False)                 # Сообщение от бота
    is_system_message = Column(Boolean, default=False)              # Системное сообщение
    is_imported = Column(Boolean, default=False)                    # Импортировано из файла
    import_source = Column(String(200), nullable=True)              # Источник импорта

    # Уникальность: (chat_id, message_id) -- один message_id в одном чате
    __table_args__ = (
        Index('ix_chat_messages_chat_thread', 'chat_id', 'thread_id'),
        Index('ix_chat_messages_chat_time', 'chat_id', 'timestamp'),
        UniqueConstraint('chat_id', 'message_id', name='uq_chat_message'),
    )


class ChatAnalyticsConfig(Base):
    """Настройки аналитики для конкретного чата"""
    __tablename__ = 'chat_analytics_configs'

    id = Column(Integer, primary_key=True)
    chat_id = Column(String(20), nullable=False, unique=True)
    chat_title = Column(String(200), nullable=True)

    # Вкл/выкл сбор
    collection_enabled = Column(Boolean, default=True)
    analysis_enabled = Column(Boolean, default=True)

    # Расписание дайджестов (cron-выражение)
    digest_cron = Column(String(100), nullable=True)        # e.g., "0 9 * * 1" (пн 09:00)
    digest_period_hours = Column(Integer, default=168)       # За какой период (default: 7 дней)
    digest_timezone = Column(String(50), default='UTC')

    # Куда отправлять дайджест
    delivery_chat_id = Column(String(20), nullable=True)     # Куда слать (chat_id)
    delivery_thread_id = Column(Integer, nullable=True)      # Конкретный topic
    delivery_to_admins = Column(Boolean, default=False)      # DM админам

    # Настроивший админ
    configured_by = Column(String(20), nullable=True)        # telegram_id

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class ChatDigest(Base):
    """Сгенерированный дайджест"""
    __tablename__ = 'chat_digests'

    id = Column(Integer, primary_key=True)
    chat_id = Column(String(20), nullable=False, index=True)

    # Период
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)

    # Контент
    summary_text = Column(Text, nullable=True)               # Полный текст дайджеста (Markdown/HTML)
    theme_count = Column(Integer, default=0)
    total_messages_analyzed = Column(Integer, default=0)

    # Метаданные генерации
    generation_time_sec = Column(Integer, nullable=True)     # Время генерации
    llm_model_used = Column(String(100), nullable=True)
    status = Column(String(20), default='pending')           # pending, generating, completed, failed
    error_message = Column(Text, nullable=True)

    # Отправка
    delivered = Column(Boolean, default=False)
    delivered_at = Column(DateTime, nullable=True)
    delivered_message_id = Column(Integer, nullable=True)    # ID сообщения в Telegram

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Связь с темами
    themes = relationship("ChatDigestTheme", back_populates="digest",
                          cascade="all, delete-orphan")


class ChatDigestTheme(Base):
    """Тема внутри дайджеста"""
    __tablename__ = 'chat_digest_themes'

    id = Column(Integer, primary_key=True)
    digest_id = Column(Integer, ForeignKey('chat_digests.id'), nullable=False)

    # Тема
    emoji = Column(String(10), nullable=True)                # Эмодзи-маркер
    title = Column(String(300), nullable=False)              # Заголовок темы
    summary = Column(Text, nullable=False)                   # Описание (2-6 предложений)

    # Связанные данные
    related_thread_ids = Column(Text, nullable=True)         # JSON: [thread_id, ...]
    key_message_links = Column(Text, nullable=True)          # JSON: ["t.me/...", ...] (1-5 ссылок)
    main_participants = Column(Text, nullable=True)          # JSON: ["username", ...]
    message_count = Column(Integer, default=0)               # Кол-во сообщений в теме

    # Порядок сортировки
    sort_order = Column(Integer, default=0)

    digest = relationship("ChatDigest", back_populates="themes")


class ChatMessageEmbedding(Base):
    """Кеш эмбеддингов для сообщений (для поиска и кластеризации)"""
    __tablename__ = 'chat_message_embeddings'

    id = Column(Integer, primary_key=True)
    chat_message_id = Column(Integer, ForeignKey('chat_messages.id'),
                             nullable=False, unique=True)
    embedding = Column(Text, nullable=False)                 # JSON float array
    model_name = Column(String(200), nullable=True)          # Какой моделью создан
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ChatImportLog(Base):
    """Лог импорта истории чата"""
    __tablename__ = 'chat_import_logs'

    id = Column(Integer, primary_key=True)
    chat_id = Column(String(20), nullable=False)
    user_telegram_id = Column(String(20), nullable=True)     # Кто импортировал
    source_filename = Column(String(500), nullable=True)
    source_format = Column(String(50), nullable=True)        # telegram_json, telegram_html, csv, txt
    messages_imported = Column(Integer, default=0)
    messages_skipped = Column(Integer, default=0)            # Дубликаты, системные и т.д.
    status = Column(String(20), default='pending')           # pending, processing, completed, failed
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
```

### ER-диаграмма (текстовая)

```
users
  |
  | (author_telegram_id)
  v
chat_messages ----< chat_message_embeddings
  |      ^
  |      | (key_message_links references)
  |      |
  |   chat_digest_themes >---- chat_digests
  |                                |
  |                                | (chat_id)
  v                                v
chat_analytics_configs        chat_import_logs
```

### Отношение к существующим таблицам

- **messages** (существующая): Простой кеш для контекста в AI-диалогах. НЕ модифицируем. Новая таблица `chat_messages` покрывает другой use case (полная история для аналитики).
- **users**: Связь по `telegram_id`. Автор сообщения ссылается на `author_telegram_id`.
- **knowledge_chunks/knowledge_bases**: НЕ используются для аналитики чатов. Это раздельные домены.

---

## 3. Основные компоненты и зоны ответственности

### Backend (backend/)

| Модуль | Путь | Ответственность |
|--------|------|-----------------|
| ChatAnalyticsRoutes | `backend/api/routes/analytics.py` | REST API для аналитики: CRUD конфигов, запуск анализа, получение дайджестов, поиск, Q&A |
| ChatAnalyticsService | `backend/services/chat_analytics_service.py` | Оркестрация: сохранение сообщений, запуск анализа, генерация дайджестов |
| ThemeClusteringService | `backend/services/theme_clustering_service.py` | Кластеризация сообщений в темы: embedding -> clustering -> LLM naming |
| DigestGeneratorService | `backend/services/digest_generator_service.py` | Генерация текста дайджеста по темам через LLM |
| ChatSearchService | `backend/services/chat_search_service.py` | Full-text + semantic search по сообщениям, Q&A mode |
| HistoryImportService | `backend/services/history_import_service.py` | Парсинг и импорт истории из файлов (JSON, HTML, CSV, TXT) |
| SchedulerService | `backend/services/scheduler_service.py` | APScheduler: управление cron-задачами для дайджестов |
| AnalyticsSchemas | `backend/schemas/analytics.py` | Pydantic модели для API запросов/ответов |

### Shared (shared/)

| Модуль | Путь | Ответственность |
|--------|------|-----------------|
| ChatAnalyticsRAG | `shared/chat_analytics_rag.py` | FAISS-индекс для сообщений чатов: embedding, поиск, кластеризация |
| Database models | `shared/database.py` | Новые ORM модели (ChatMessage, ChatAnalyticsConfig, etc.) |
| Chat history parsers | `shared/document_loaders/chat_history_parser.py` | Парсеры для Telegram JSON/HTML export, CSV, TXT |

### Frontend (frontend/)

| Модуль | Путь | Ответственность |
|--------|------|-----------------|
| MessageCollectorHandler | `frontend/bot_handlers.py` (расширение) | Перехват сообщений в группах и отправка на backend |
| AnalyticsCallbacks | `frontend/bot_callbacks.py` (расширение) | UI для админ-панели аналитики (кнопки, менюшки) |
| BackendClient | `frontend/backend_client.py` (расширение) | Новые методы для analytics API |
| AnalyticsButtons | `frontend/templates/buttons.py` (расширение) | Кнопки для меню аналитики |

### Диаграмма зависимостей компонентов

```
frontend/
  bot_handlers.py (MessageCollectorHandler)
       |
       | httpx (backend_client)
       v
backend/api/routes/analytics.py
       |
       +---> ChatAnalyticsService
       |         |
       |         +---> ThemeClusteringService
       |         |         |
       |         |         +---> shared/chat_analytics_rag.py (embeddings + FAISS)
       |         |         +---> shared/ai_providers.py (LLM for naming)
       |         |
       |         +---> DigestGeneratorService
       |         |         |
       |         |         +---> shared/ai_providers.py (LLM for summaries)
       |         |
       |         +---> ChatSearchService
       |         |         |
       |         |         +---> shared/chat_analytics_rag.py (semantic search)
       |         |
       |         +---> HistoryImportService
       |                   |
       |                   +---> shared/document_loaders/chat_history_parser.py
       |
       +---> SchedulerService
                 |
                 +---> APScheduler (triggers ChatAnalyticsService)
```

---

## 4. Потоки данных

### 4.1. Сбор сообщений (Message Collection)

```
1. Пользователь отправляет сообщение в supergroup/topic
2. python-telegram-bot получает Update через polling
3. MessageCollectorHandler (frontend) проверяет:
   - Это группа/супергруппа? (chat.type in ['group', 'supergroup'])
   - Сбор включен для этого chat_id?
   - Сообщение НЕ от бота и НЕ системное?
4. Формирует payload: chat_id, thread_id, message_id, author_*, text, timestamp, link
5. Асинхронно отправляет POST /api/v1/analytics/messages на backend
6. Backend: ChatAnalyticsService.store_message() сохраняет в chat_messages
7. Если текст >= MIN_TEXT_LENGTH (напр., 10 символов):
   - Создает embedding и сохраняет в chat_message_embeddings
   - Добавляет в in-memory FAISS индекс (если загружен)
```

**Важно**: Сбор сообщений должен быть максимально легковесным и не тормозить основной поток бота. Отправка на backend -- fire-and-forget (asyncio.create_task, без await результата).

### 4.2. Импорт истории (History Import)

```
1. Админ: нажимает "Импорт истории" в админ-панели аналитики
2. Бот просит отправить файл (JSON, HTML, TXT, CSV)
3. Админ отправляет файл
4. Frontend: скачивает файл, отправляет POST /api/v1/analytics/import
   с multipart: file + chat_id + format_hint
5. Backend: HistoryImportService:
   a. Определяет формат (auto-detect или по hint)
   b. Парсит файл через chat_history_parser.py
   c. Для каждого сообщения: dedup по (chat_id, message_id) или (chat_id, author, timestamp, text_hash)
   d. Помечает is_imported=True, import_source=filename
   e. Batch insert в chat_messages
   f. Batch embedding generation (в фоне, через Job)
6. Возвращает статистику: imported=N, skipped=M, errors=K
```

### 4.3. Анализ тем (Topic Analysis)

```
1. Триггер: cron-задача SchedulerService ИЛИ ручной запуск админом
2. ChatAnalyticsService.run_analysis(chat_id, period_start, period_end):
   a. Загрузить все chat_messages за период (фильтр: не бот, не системное, не пустое)
   b. Загрузить/создать embeddings для этих сообщений

3. ThemeClusteringService.cluster_messages(messages, embeddings):
   a. Нормализация: объединить короткие сообщения в "диалоговые блоки" по proximity (thread_id + время)
   b. Кластеризация embeddings:
      - HDBSCAN (адаптивное кол-во кластеров) или Agglomerative Clustering
      - min_cluster_size зависит от объема (настраиваемый)
   c. Для каждого кластера:
      - Выбрать top-N представительных сообщений (ближайшие к центроиду)
      - Отправить LLM-запрос для генерации:
        * Заголовок темы
        * Краткое описание (2-6 предложений)
        * Emoji-маркер
      - Определить key_message_links (1-5 наиболее важных)
      - Определить main_participants (по частоте)
      - Определить related_thread_ids
   d. Вернуть List[ThemeData]

4. DigestGeneratorService.generate_digest(themes, period, chat_info):
   a. Собрать все темы в единый контекст
   b. Запросить LLM: сгенерировать блок #summary:
      - Количество тем
      - Ключевые решения
      - Нерешенные вопросы
      - Итог периода
   c. Сформировать финальный текст дайджеста (Markdown/HTML)
   d. Сохранить ChatDigest + ChatDigestTheme записи

5. Доставка: отправить в настроенный канал (см. 4.5)
```

### 4.4. Поиск и Q&A

```
Полнотекстовый + семантический поиск:
1. Пользователь: /search_chat <query> [--period=7d] [--topic=N] [--author=@user]
2. Frontend -> POST /api/v1/analytics/search
3. ChatSearchService.search(query, filters):
   a. Semantic: embed(query) -> FAISS search in chat_messages
   b. Full-text: BM25 search in chat_messages.text (SQL LIKE или BM25 in-memory)
   c. RRF fusion результатов
   d. Применить фильтры (period, topic/thread_id, author)
   e. Для top-K результатов: собрать контекст (предыдущие/следующие сообщения)
   f. Вернуть: [{text, author, timestamp, message_link, context_snippet, score}]

Q&A режим:
1. Пользователь: /ask_chat <вопрос>
2. Frontend -> POST /api/v1/analytics/qa
3. ChatSearchService.answer_question(question, chat_id, filters):
   a. Найти top-K релевантных сообщений (search выше)
   b. Если top-K score < threshold -> вернуть "Недостаточно информации"
   c. Сформировать промпт для LLM:
      - Контекст: найденные сообщения с метаданными
      - Инструкция: "Ответь ТОЛЬКО на основе предоставленных сообщений.
        Укажи ссылки на источники. Если информации недостаточно -- скажи об этом."
   d. LLM генерирует ответ
   e. Post-processing: strip_unknown_citations (reuse rag_safety.py)
   f. Вернуть: {answer, source_messages: [{text, link, author}]}
```

### 4.5. Расписание и доставка дайджеста

```
1. При старте backend: SchedulerService.init():
   a. Загрузить все ChatAnalyticsConfig с digest_cron != null
   b. Для каждого: зарегистрировать CronTrigger в APScheduler

2. По срабатыванию cron:
   a. SchedulerService вызывает ChatAnalyticsService.run_analysis(...)
   b. После генерации: доставка дайджеста
   c. Вариант delivery:
      - delivery_chat_id + delivery_thread_id -> bot отправляет в группу/тему
      - delivery_to_admins -> bot отправляет DM каждому админу
   d. Для отправки: backend делает callback на bot через Redis pub/sub
      ИЛИ bot polling endpoint на backend

3. Ручной триггер:
   a. Админ: кнопка "Сгенерировать дайджест сейчас"
   b. Frontend -> POST /api/v1/analytics/digests/generate
      с параметрами: chat_id, period_start, period_end
   c. Backend: запуск в фоновом потоке
   d. Возвращает digest_id для отслеживания статуса
```

**Механизм доставки (backend -> bot):**

Проблема: backend не имеет прямого доступа к Telegram API. Решения:

| Вариант | Плюсы | Минусы | Выбор |
|---------|-------|--------|-------|
| A. Bot polling: периодически GET /api/v1/analytics/digests/pending | Простой, без новых зависимостей | Задержка до следующего poll | -- |
| B. Redis Pub/Sub: backend публикует, bot подписывается | Мгновенная доставка, Redis уже есть | Нужна подписка в bot | **Выбран** |
| C. Webhook: backend -> bot HTTP callback | Мгновенно | Нужен HTTP-сервер в bot, сложнее | -- |

**Выбор: Вариант B -- Redis Pub/Sub.**

```
Backend: redis.publish("digest_ready", json.dumps({"digest_id": 123, "chat_id": "-100xxx"}))
Bot: redis subscriber thread -> получает -> отправляет в Telegram
```

---

## 5. API / границы модулей

### Новые Backend API endpoints

Все под префиксом `/api/v1/analytics/`.

#### 5.1. Сообщения

```
POST /api/v1/analytics/messages
  Body: {
    "chat_id": str,
    "thread_id": int | null,
    "message_id": int,
    "author_telegram_id": str,
    "author_username": str | null,
    "author_display_name": str | null,
    "text": str,
    "timestamp": str (ISO 8601),
    "message_link": str | null,
    "is_bot_message": bool,
    "is_system_message": bool
  }
  Response: {"status": "ok", "id": int}

  Примечание: вызывается для каждого сообщения из группы.
  Должен быть максимально быстрым (<50ms).
```

```
POST /api/v1/analytics/messages/batch
  Body: {
    "messages": [... same as above ...]
  }
  Response: {"status": "ok", "stored": int, "skipped": int}

  Примечание: для batch-загрузки при импорте.
```

#### 5.2. Конфигурация аналитики

```
GET /api/v1/analytics/configs
  Query: chat_id (optional)
  Response: [ChatAnalyticsConfigResponse, ...]

GET /api/v1/analytics/configs/{chat_id}
  Response: ChatAnalyticsConfigResponse

PUT /api/v1/analytics/configs/{chat_id}
  Body: {
    "collection_enabled": bool,
    "analysis_enabled": bool,
    "digest_cron": str | null,
    "digest_period_hours": int,
    "digest_timezone": str,
    "delivery_chat_id": str | null,
    "delivery_thread_id": int | null,
    "delivery_to_admins": bool
  }
  Response: ChatAnalyticsConfigResponse

DELETE /api/v1/analytics/configs/{chat_id}
  Response: {"status": "deleted"}
```

#### 5.3. Дайджесты

```
POST /api/v1/analytics/digests/generate
  Body: {
    "chat_id": str,
    "period_start": str (ISO 8601),
    "period_end": str (ISO 8601)
  }
  Response: {"digest_id": int, "status": "pending"}

GET /api/v1/analytics/digests/{digest_id}
  Response: ChatDigestResponse (with themes)

GET /api/v1/analytics/digests
  Query: chat_id, status, limit, offset
  Response: [ChatDigestResponse, ...]
```

#### 5.4. Поиск

```
POST /api/v1/analytics/search
  Body: {
    "query": str,
    "chat_id": str,
    "thread_id": int | null,
    "author_telegram_id": str | null,
    "period_start": str | null,
    "period_end": str | null,
    "top_k": int = 10
  }
  Response: {
    "results": [
      {
        "message_id": int,
        "text": str,
        "author": str,
        "timestamp": str,
        "message_link": str,
        "thread_id": int | null,
        "context_before": str | null,
        "context_after": str | null,
        "score": float
      }
    ],
    "total_found": int
  }
```

#### 5.5. Q&A

```
POST /api/v1/analytics/qa
  Body: {
    "question": str,
    "chat_id": str,
    "thread_id": int | null,
    "period_start": str | null,
    "period_end": str | null
  }
  Response: {
    "answer": str,
    "sources": [
      {
        "text": str,
        "author": str,
        "message_link": str,
        "timestamp": str
      }
    ],
    "confidence": str  // "high", "medium", "low", "insufficient"
  }
```

#### 5.6. Импорт истории

```
POST /api/v1/analytics/import
  Multipart:
    file: UploadFile
    chat_id: str
    format_hint: str | null  (telegram_json, telegram_html, csv, txt)
  Response: {
    "import_id": int,
    "status": "processing",
    "messages_found": int
  }

GET /api/v1/analytics/import/{import_id}
  Response: ChatImportLogResponse
```

#### 5.7. Статистика

```
GET /api/v1/analytics/stats/{chat_id}
  Query: period_start, period_end (optional)
  Response: {
    "total_messages": int,
    "unique_authors": int,
    "active_threads": int,
    "messages_per_day": float,
    "top_authors": [{"name": str, "count": int}],
    "period": {"start": str, "end": str}
  }
```

### Pydantic Schemas (backend/schemas/analytics.py)

```python
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class MessagePayload(BaseModel):
    chat_id: str
    thread_id: Optional[int] = None
    message_id: int
    author_telegram_id: str
    author_username: Optional[str] = None
    author_display_name: Optional[str] = None
    text: str
    timestamp: str
    message_link: Optional[str] = None
    is_bot_message: bool = False
    is_system_message: bool = False


class MessageBatchPayload(BaseModel):
    messages: List[MessagePayload]


class AnalyticsConfigUpdate(BaseModel):
    collection_enabled: Optional[bool] = None
    analysis_enabled: Optional[bool] = None
    digest_cron: Optional[str] = None
    digest_period_hours: Optional[int] = None
    digest_timezone: Optional[str] = None
    delivery_chat_id: Optional[str] = None
    delivery_thread_id: Optional[int] = None
    delivery_to_admins: Optional[bool] = None


class DigestGenerateRequest(BaseModel):
    chat_id: str
    period_start: str
    period_end: str


class SearchRequest(BaseModel):
    query: str
    chat_id: str
    thread_id: Optional[int] = None
    author_telegram_id: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    top_k: int = 10


class QARequest(BaseModel):
    question: str
    chat_id: str
    thread_id: Optional[int] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
```

---

## 6. Bot UI Flow

### 6.1. Админ-панель аналитики

Добавляется в существующее admin_menu (frontend/templates/buttons.py):

```
Админ-панель
  |
  +-- [Существующие пункты...]
  +-- "Аналитика чатов" (callback: admin_analytics)
        |
        +-- "Настроить чат" (callback: analytics_select_chat)
        |     |
        |     +-- [Список чатов, где бот добавлен]
        |           |
        |           +-- "Сбор сообщений: ВКЛ/ВЫКЛ" (toggle)
        |           +-- "Расписание дайджеста" (callback: analytics_schedule)
        |           |     |
        |           |     +-- "Ежедневно" / "Еженедельно" / "Свой cron"
        |           |     +-- "Период: 24ч / 7д / 30д / свой"
        |           |     +-- "Канал доставки: этот чат / другой / DM"
        |           |
        |           +-- "Сгенерировать сейчас" (callback: analytics_generate_now)
        |                 |
        |                 +-- "За последние 24ч / 7д / 30д / указать даты"
        |
        +-- "Импорт истории" (callback: analytics_import)
        |     |
        |     +-- "Выберите чат" -> "Отправьте файл экспорта"
        |
        +-- "Просмотр дайджестов" (callback: analytics_digests)
        |     |
        |     +-- [Список последних дайджестов]
        |           +-- [Просмотр конкретного дайджеста]
        |
        +-- "Статистика" (callback: analytics_stats)
              |
              +-- [Выбор чата] -> [Статистика за период]
```

### 6.2. Команды для пользователей (в группе)

```
/digest          -- Показать последний дайджест для этого чата
/digest 7d       -- Дайджест за последние 7 дней (ручной запрос)
/search_chat <q> -- Поиск по истории чата
/ask_chat <q>    -- Задать вопрос по истории чата
```

Эти команды обрабатываются в bot_handlers.py через CommandHandler и направляются на backend.

### 6.3. Пример вывода дайджеста

```
#summary за 01.02 - 07.02.2026

Обсуждено 5 тем, 342 сообщения от 18 участников.

Ключевые решения:
- Утвержден план миграции на PostgreSQL
- Выбран формат API v2

Нерешенные вопросы:
- Сроки нагрузочного тестирования
- Бюджет на инфраструктуру

---

1. :wrench: Миграция базы данных
Обсуждение перехода с MySQL на PostgreSQL. Проведен анализ
совместимости запросов, выбран план поэтапной миграции с
параллельной работой двух БД на переходный период.
Участники: @alice, @bob, @charlie
Темы: #infrastructure, #backend-dev
Ссылки: t.me/c/123/456, t.me/c/123/789

2. :rocket: Релиз v2.0
Обсуждение плана релиза. Финализирован список фич для включения,
определены даты feature freeze и RC.
Участники: @alice, @dave
Ссылки: t.me/c/123/101

[... и т.д. ...]
```

---

## 7. Стратегия сбора сообщений

### Основной подход: python-telegram-bot MessageHandler

Бот уже использует `python-telegram-bot==22.5` с polling (`app.run_polling()`). Все сообщения проходят через Update handlers.

**Текущие handlers (bot.py):**
```python
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
```

**Проблема:** Текущий фильтр `~filters.COMMAND` пропускает только не-командные текстовые сообщения. Для сбора в группах нужен отдельный handler с более широким фильтром.

**Решение: Добавить MessageCollectorHandler с высоким group number.**

```python
# В frontend/bot.py, ПОСЛЕ основных handlers:

from frontend.bot_handlers import message_collector

# group=10 -- выполняется ПОСЛЕ основных handlers (group=0 по умолчанию)
# Это означает, что collector НЕ мешает обычной обработке
app.add_handler(
    MessageHandler(
        filters.TEXT & (filters.ChatType.SUPERGROUP | filters.ChatType.GROUP),
        message_collector,
    ),
    group=10,
)
```

```python
# В frontend/bot_handlers.py:

async def message_collector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Перехват всех текстовых сообщений в группах для аналитики.

    Запускается в group=10 (после основных handlers).
    Fire-and-forget: не блокирует основной flow.
    """
    msg = update.effective_message
    if not msg or not msg.text:
        return

    chat = update.effective_chat
    if not chat or chat.type not in ('group', 'supergroup'):
        return

    user = update.effective_user

    # Формируем payload
    payload = {
        "chat_id": str(chat.id),
        "thread_id": getattr(msg, "message_thread_id", None),
        "message_id": msg.message_id,
        "author_telegram_id": str(user.id) if user else None,
        "author_username": user.username if user else None,
        "author_display_name": user.full_name if user else None,
        "text": msg.text,
        "timestamp": msg.date.isoformat() if msg.date else None,
        "message_link": _build_message_link(chat.id, msg.message_id),
        "is_bot_message": user.is_bot if user else False,
        "is_system_message": False,  # Текстовые сообщения не системные
    }

    # Fire-and-forget: отправляем на backend без ожидания
    asyncio.create_task(_send_to_analytics(payload))


async def _send_to_analytics(payload: dict) -> None:
    """Отправить сообщение на backend для аналитики (fire-and-forget)."""
    try:
        await asyncio.to_thread(backend_client.analytics_store_message, payload)
    except Exception as e:
        logger.debug("Analytics store_message failed: %s", e)


def _build_message_link(chat_id: int, message_id: int) -> str:
    """Построить ссылку на сообщение в Telegram."""
    # Для supergroups: chat_id начинается с -100
    clean_id = str(chat_id).replace("-100", "")
    return f"https://t.me/c/{clean_id}/{message_id}"
```

### Ограничения polling-подхода

| Ограничение | Описание | Митигация |
|-------------|----------|-----------|
| Только новые сообщения | Polling не дает историю до добавления бота | Импорт истории из файлов |
| Бот должен видеть все сообщения | Требует отключения Privacy Mode | Документация: "Отключите Privacy Mode в @BotFather" |
| Не все типы контента | Фото, видео, файлы без текста пропускаются | Для MVP фокус на текстовых сообщениях. Позже -- caption для медиа. |
| Редактирования | Отредактированные сообщения не обновляются | Можно добавить EditedMessageHandler позже |

### Альтернативный подход (НЕ используем, но документируем)

**Telegram Bot API getChatHistory**: Не существует в Bot API. Есть в Telegram Client API (Telethon/Pyrogram), но это требует user account, а не bot token.

**Telegram Bot API getUpdates**: Это и есть polling, уже используемый python-telegram-bot.

**Вывод:** Polling через python-telegram-bot + импорт истории из файлов -- единственный реалистичный подход для bot-token.

---

## 8. Подход к кластеризации и тематизации

### Пайплайн

```
Сообщения за период
       |
       v
[1. Preprocessing]
       |
       v
[2. Embedding]
       |
       v
[3. Clustering]
       |
       v
[4. Theme Extraction]
       |
       v
[5. Summary Generation]
```

### Шаг 1: Preprocessing

```python
def preprocess_messages(messages: List[ChatMessage]) -> List[MessageBlock]:
    """
    Объединение коротких сообщений в блоки для лучшей кластеризации.

    Стратегия:
    - Сообщения в одном thread_id от одного автора, отправленные < 5 мин друг от друга,
      объединяются в один блок.
    - Блоки < 20 символов отбрасываются (приветствия, +1, ок и т.п.)
    """
    blocks = []
    current_block = None

    for msg in sorted(messages, key=lambda m: (m.thread_id or 0, m.timestamp)):
        if should_merge(current_block, msg):
            current_block.text += "\n" + msg.text
            current_block.message_ids.append(msg.message_id)
        else:
            if current_block and len(current_block.text) >= MIN_BLOCK_LENGTH:
                blocks.append(current_block)
            current_block = MessageBlock(msg)

    return blocks
```

### Шаг 2: Embedding

Переиспользуем **тот же sentence-transformers encoder** (`intfloat/multilingual-e5-base`), что уже загружен в RAGSystem.

```python
# В shared/chat_analytics_rag.py:

class ChatAnalyticsRAG:
    def __init__(self, rag_system: RAGSystem):
        """
        Использует encoder и reranker из существующего RAGSystem,
        но хранит отдельные FAISS-индексы per chat_id.
        """
        self.encoder = rag_system.encoder  # Shared! Не создаем копию
        self.reranker = rag_system.reranker
        self.dimension = rag_system.dimension
        self.indices: Dict[str, faiss.Index] = {}  # chat_id -> FAISS index
        self.chunks: Dict[str, List[ChatMessage]] = {}

    def embed_messages(self, texts: List[str]) -> np.ndarray:
        """Batch embed для сообщений."""
        if not self.encoder:
            raise RuntimeError("Encoder not available")
        embeddings = self.encoder.encode(texts, convert_to_numpy=True,
                                          show_progress_bar=False,
                                          batch_size=64)
        faiss.normalize_L2(embeddings)
        return embeddings
```

### Шаг 3: Clustering

```python
def cluster_messages(embeddings: np.ndarray,
                     min_cluster_size: int = 5,
                     method: str = "hdbscan") -> np.ndarray:
    """
    Кластеризация эмбеддингов сообщений.

    Args:
        embeddings: (N, D) array, L2-normalized
        min_cluster_size: минимальное кол-во сообщений в кластере
        method: "hdbscan" или "agglomerative"

    Returns:
        labels: (N,) array, -1 для шумовых точек
    """
    if method == "hdbscan":
        import hdbscan
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=min_cluster_size,
            min_samples=2,
            metric='euclidean',  # L2-normalized -> euclidean ~ cosine
            cluster_selection_method='eom',
        )
        labels = clusterer.fit_predict(embeddings)
    else:
        from sklearn.cluster import AgglomerativeClustering
        # Автоматическое определение числа кластеров через distance_threshold
        clusterer = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=1.2,  # Для L2-normalized: ~cosine dist 0.72
            metric='euclidean',
            linkage='ward',
        )
        labels = clusterer.fit_predict(embeddings)

    return labels
```

**Выбор алгоритма:**

| Алгоритм | Плюсы | Минусы | Когда |
|----------|-------|--------|-------|
| HDBSCAN | Адаптивный, находит шум, не требует N кластеров | Нужна доп. зависимость, медленнее на малых данных | >100 сообщений |
| Agglomerative | В sklearn, стабильный, нет "шумовых" точек | Нужен distance_threshold | <100 сообщений |
| K-Means | Быстрый | Нужно задать K заранее | Не подходит (K неизвестен) |

**Рекомендация:** HDBSCAN как основной, Agglomerative как fallback.

### Шаг 4: Theme Extraction (LLM)

```python
async def extract_theme(cluster_messages: List[MessageBlock],
                        cluster_id: int) -> ThemeData:
    """Извлечь тему из кластера через LLM."""

    # Берем top-10 представительных сообщений (ближайших к центроиду)
    representative = select_representative_messages(cluster_messages, top_n=10)

    prompt = f"""Analyze the following cluster of chat messages and extract the main theme.

Messages:
{format_messages_for_prompt(representative)}

Respond in the SAME LANGUAGE as the messages. Return JSON:
{{
  "emoji": "<single emoji that represents the theme>",
  "title": "<concise theme title, max 10 words>",
  "summary": "<2-6 sentence description of the discussion>",
  "key_decisions": ["<decision 1>", ...] or [],
  "unresolved_questions": ["<question 1>", ...] or []
}}

Be factual. Do not add information not present in the messages.
Return ONLY valid JSON."""

    response = ai_manager.query(prompt)
    return parse_theme_response(response, cluster_messages)
```

### Шаг 5: Sizing and performance

| Метрика | 1K сообщений | 5K | 10K |
|---------|-------------|-----|------|
| Embedding (batch, GPU) | ~2 sec | ~8 sec | ~15 sec |
| Embedding (batch, CPU) | ~10 sec | ~45 sec | ~90 sec |
| HDBSCAN clustering | ~0.5 sec | ~2 sec | ~5 sec |
| LLM calls (10 themes) | ~30 sec | ~45 sec | ~60 sec |
| **Total (GPU)** | **~35 sec** | **~55 sec** | **~80 sec** |
| **Total (CPU)** | **~45 sec** | **~95 sec** | **~160 sec** |

Целевое NFR: 10K сообщений < 3 минут -- **достижимо**.

---

## 9. Система расписаний

### APScheduler в backend

```python
# backend/services/scheduler_service.py

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
import logging

logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self, db_url: str):
        jobstores = {
            'default': SQLAlchemyJobStore(url=db_url,
                                          tablename='apscheduler_jobs')
        }
        self.scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            job_defaults={
                'coalesce': True,       # Объединить пропущенные запуски
                'max_instances': 1,      # Не запускать параллельно
                'misfire_grace_time': 3600,  # 1 час grace time
            }
        )
        self._analytics_service = None  # Lazy inject

    def start(self):
        """Запустить scheduler и загрузить задачи из БД."""
        self.scheduler.start()
        self._sync_jobs_from_db()
        logger.info("SchedulerService started")

    def _sync_jobs_from_db(self):
        """Синхронизировать APScheduler jobs с ChatAnalyticsConfig."""
        from shared.database import get_session, ChatAnalyticsConfig

        with get_session() as session:
            configs = session.query(ChatAnalyticsConfig).filter(
                ChatAnalyticsConfig.digest_cron.isnot(None),
                ChatAnalyticsConfig.analysis_enabled == True,
            ).all()

        existing_job_ids = {job.id for job in self.scheduler.get_jobs()}

        for config in configs:
            job_id = f"digest_{config.chat_id}"
            if job_id in existing_job_ids:
                # Обновить trigger если cron изменился
                self.scheduler.reschedule_job(
                    job_id,
                    trigger=CronTrigger.from_crontab(
                        config.digest_cron,
                        timezone=config.digest_timezone or 'UTC'
                    ),
                )
            else:
                self.scheduler.add_job(
                    self._run_digest,
                    trigger=CronTrigger.from_crontab(
                        config.digest_cron,
                        timezone=config.digest_timezone or 'UTC'
                    ),
                    id=job_id,
                    args=[config.chat_id, config.digest_period_hours],
                    replace_existing=True,
                )

    async def _run_digest(self, chat_id: str, period_hours: int):
        """Callback для APScheduler: запуск генерации дайджеста."""
        from datetime import datetime, timedelta, timezone

        period_end = datetime.now(timezone.utc)
        period_start = period_end - timedelta(hours=period_hours)

        try:
            digest_id = await self._analytics_service.run_analysis(
                chat_id=chat_id,
                period_start=period_start,
                period_end=period_end,
            )
            logger.info("Digest generated: chat_id=%s, digest_id=%s", chat_id, digest_id)

            # Уведомить бота через Redis
            await self._notify_bot(chat_id, digest_id)
        except Exception as e:
            logger.error("Digest generation failed: chat_id=%s, error=%s", chat_id, e, exc_info=True)

    async def _notify_bot(self, chat_id: str, digest_id: int):
        """Отправить уведомление боту через Redis Pub/Sub."""
        import json
        try:
            from shared.config import REDIS_HOST, REDIS_PORT, REDIS_ENABLED
            if not REDIS_ENABLED:
                logger.warning("Redis not enabled, cannot notify bot about digest")
                return
            import redis.asyncio as aioredis
            r = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT)
            await r.publish("digest_ready", json.dumps({
                "digest_id": digest_id,
                "chat_id": chat_id,
            }))
            await r.close()
        except Exception as e:
            logger.error("Failed to notify bot: %s", e)

    def upsert_schedule(self, chat_id: str, cron_expr: str,
                        period_hours: int, timezone_str: str = 'UTC'):
        """Добавить или обновить расписание дайджеста."""
        job_id = f"digest_{chat_id}"
        self.scheduler.add_job(
            self._run_digest,
            trigger=CronTrigger.from_crontab(cron_expr, timezone=timezone_str),
            id=job_id,
            args=[chat_id, period_hours],
            replace_existing=True,
        )

    def remove_schedule(self, chat_id: str):
        """Удалить расписание дайджеста."""
        job_id = f"digest_{chat_id}"
        try:
            self.scheduler.remove_job(job_id)
        except Exception:
            pass

    def list_schedules(self) -> list:
        """Получить список всех активных расписаний."""
        return [
            {"id": job.id, "next_run": str(job.next_run_time), "trigger": str(job.trigger)}
            for job in self.scheduler.get_jobs()
        ]
```

### Интеграция в backend app startup

```python
# В backend/app.py:

from backend.services.scheduler_service import SchedulerService

def create_app() -> FastAPI:
    app = FastAPI(title=settings.APP_NAME)

    # ... existing router includes ...

    # Новый router для аналитики
    from backend.api.routes.analytics import router as analytics_router
    app.include_router(analytics_router, prefix=prefix)

    @app.on_event("startup")
    def _start_scheduler() -> None:
        from shared.database import db_url
        scheduler = SchedulerService(db_url)
        app.state.scheduler = scheduler
        scheduler.start()

    @app.on_event("shutdown")
    def _stop_scheduler() -> None:
        if hasattr(app.state, 'scheduler'):
            app.state.scheduler.scheduler.shutdown()

    return app
```

### Новые зависимости

```
# requirements.txt (additions):
apscheduler>=3.10.0
hdbscan>=0.8.33
scikit-learn>=1.3.0          # Для AgglomerativeClustering (fallback)
redis[hiredis]>=5.0.0        # Async Redis для pub/sub (обновление от redis==7.1.0)
```

---

## 10. Интеграция с существующим RAG

### Что можно переиспользовать

| Компонент | Файл | Переиспользование | Комментарий |
|-----------|------|--------------------|-------------|
| Sentence-transformers encoder | `shared/rag_system.py` (self.encoder) | **Да, полностью** | Один и тот же encoder для document chunks и chat messages |
| Cross-encoder reranker | `shared/rag_system.py` (self.reranker) | **Да** | Для reranking в chat search |
| FAISS indexing | `shared/rag_system.py` (_load_index) | **Нет, отдельный** | Разный lifecycle, разные данные |
| BM25 search | `shared/rag_system.py` (_bm25_search, _rrf_fuse) | **Да, паттерн** | Скопировать логику, адаптировать для chat_messages |
| LLM integration | `shared/ai_providers.py` (ai_manager) | **Да, полностью** | Тот же Ollama/OpenAI для генерации саммари |
| Safety filters | `shared/rag_safety.py` | **Да** | strip_unknown_citations, sanitize_commands для ответов Q&A |
| Document loaders | `shared/document_loaders/chat_loader.py` | **Частично** | Расширить для полного парсинга (сейчас слишком упрощенный) |
| Chunking | `shared/document_loaders/chunking.py` | **Нет** | Chat messages не нуждаются в chunking -- они уже "чанки" |
| Embedding pipeline | `shared/rag_pipeline/embedder.py` | **Да** | embed_texts() для batch embedding |
| Reranking pipeline | `shared/rag_pipeline/reranker.py` | **Да** | rerank() для результатов поиска |

### Архитектура ChatAnalyticsRAG

```python
# shared/chat_analytics_rag.py

class ChatAnalyticsRAG:
    """
    RAG для сообщений чатов.
    Переиспользует encoder/reranker из основного RAGSystem,
    но хранит отдельные FAISS-индексы per chat.
    """

    def __init__(self):
        # Lazy init: берем encoder из rag_system при первом использовании
        self._encoder = None
        self._reranker = None
        self._dimension = None

        # Per-chat indices
        self.faiss_indices: Dict[str, faiss.Index] = {}
        self.message_cache: Dict[str, List[ChatMessage]] = {}

        # BM25 per-chat
        self.bm25_indices: Dict[str, Dict] = {}

    @property
    def encoder(self):
        if self._encoder is None:
            from shared.rag_system import rag_system
            self._encoder = rag_system.encoder
            self._dimension = rag_system.dimension
            self._reranker = rag_system.reranker
        return self._encoder

    def build_index(self, chat_id: str, messages: List[ChatMessage],
                    embeddings: np.ndarray):
        """Построить FAISS индекс для чата."""
        if embeddings.shape[0] == 0:
            return

        emb = embeddings.astype('float32')
        faiss.normalize_L2(emb)

        index = faiss.IndexFlatIP(emb.shape[1])
        index.add(emb)

        self.faiss_indices[chat_id] = index
        self.message_cache[chat_id] = messages
        # BM25 тоже строим
        self.bm25_indices[chat_id] = self._build_bm25(messages)

    def search(self, query: str, chat_id: str, top_k: int = 10,
               filters: Optional[dict] = None) -> List[dict]:
        """Гибридный поиск: semantic + BM25 + RRF fusion."""
        # 1. Semantic search
        semantic_results = self._semantic_search(query, chat_id, top_k * 3)

        # 2. BM25 search
        bm25_results = self._bm25_search(query, chat_id, top_k * 3)

        # 3. RRF fusion
        fused = self._rrf_fuse([semantic_results, bm25_results])

        # 4. Apply filters
        if filters:
            fused = self._apply_filters(fused, filters)

        # 5. Rerank top candidates
        if self._reranker and len(fused) > top_k:
            fused = self._rerank(query, fused[:top_k * 2], top_k)

        return fused[:top_k]
```

### Embedding storage strategy

**Вариант A (выбран): DB + in-memory FAISS rebuild**
- Embeddings хранятся в `chat_message_embeddings` (JSON)
- При старте / по запросу: загрузить из БД, построить FAISS
- Плюсы: Persistent, не теряется при перезапуске
- Минусы: Медленный rebuild для больших чатов

**Вариант B: FAISS index на диск**
- Сохранять faiss.write_index() / faiss.read_index()
- Плюсы: Быстрый загрузка
- Минусы: Нужно синхронизировать с БД

**Рекомендация:** Вариант A для MVP, Вариант B как оптимизация при > 50K сообщений в чате.

---

## 11. Риски и митигации

| # | Риск | Вероятность | Импакт | Митигация |
|---|------|-------------|--------|-----------|
| R1 | **Пропуск сообщений при polling** -- бот может не видеть все сообщения (Privacy Mode, баги networking) | Средняя | Высокий | Документировать настройку Privacy Mode. Добавить мониторинг: если gap в message_id -- предупредить. Импорт истории как fallback. |
| R2 | **Нагрузка на БД при высоком трафике** -- 1000+ сообщений/мин в активном чате | Низкая | Средний | Batch insert (буферизация 5 сек в frontend), async/fire-and-forget. Для MySQL: connection pool уже настроен. |
| R3 | **Галлюцинации LLM в дайджестах** -- LLM может добавить несуществующую информацию | Средняя | Высокий | Prompt engineering: "ONLY from provided messages". Post-processing: проверка цитат. Человеческая валидация через preview перед отправкой. |
| R4 | **Плохая кластеризация для малого кол-ва сообщений** -- HDBSCAN может не найти кластеры при < 20 сообщениях | Средняя | Низкий | Fallback: если < N сообщений, использовать thread-based группировку вместо semantic clustering. |
| R5 | **Высокое потребление памяти FAISS** -- при 100K+ сообщений в чате | Низкая | Средний | Lazy loading: индекс строится только при запросе. TTL для неактивных индексов. Для больших чатов: IVF индекс вместо Flat. |
| R6 | **APScheduler потеря задач при перезапуске** -- in-memory scheduler | Низкая | Средний | SQLAlchemyJobStore для persistence. coalesce=True для пропущенных запусков. |
| R7 | **Privacy: хранение всех сообщений** -- GDPR / чувствительные данные | Средняя | Высокий | Opt-in (collection_enabled по умолчанию False, требует явного включения админом). Команда /delete_my_data. Retention policy: автоочистка сообщений старше N дней. |
| R8 | **Таймаут LLM при генерации дайджеста для больших периодов** -- 20+ тем, каждая требует LLM call | Средняя | Средний | Parallel LLM calls (asyncio.gather). Ограничение max_themes=15. Chunking тем для промпта. |
| R9 | **Дублирование сообщений при импорте** -- один и тот же экспорт загружен дважды | Низкая | Низкий | Дедупликация по UniqueConstraint(chat_id, message_id). Для импорта без message_id: hash(author + timestamp + text_prefix). |
| R10 | **Конфликт scheduler при горизонтальном масштабировании** -- два backend instance запускают один и тот же дайджест | Низкая | Средний | Для MVP: uvicorn workers=1. Для scale: Redis-based distributed lock или переход на Celery Beat. |

---

## 12. План имплементации

### Фаза 0: Подготовка (1-2 дня)

**Цель:** Инфраструктура для новой фичи без изменения существующей функциональности.

| Шаг | Что делать | Файлы | Сложность |
|-----|-----------|-------|-----------|
| 0.1 | Добавить новые ORM модели в shared/database.py | `shared/database.py` | Низкая |
| 0.2 | Миграция: create_all для новых таблиц | `shared/database.py` (migrate_database) | Низкая |
| 0.3 | Добавить зависимости: apscheduler, hdbscan, scikit-learn | `requirements.txt`, `Dockerfile` | Низкая |
| 0.4 | Создать пустые module files для нового кода | `backend/services/chat_*.py`, `backend/api/routes/analytics.py`, `backend/schemas/analytics.py`, `shared/chat_analytics_rag.py` | Низкая |
| 0.5 | Добавить конфиг-переменные (ANALYTICS_ENABLED, etc.) | `shared/config.py`, `env.template` | Низкая |

### Фаза 1: Сбор сообщений (2-3 дня)

**Цель:** Бот начинает собирать сообщения из групп и сохранять в БД.

| Шаг | Что делать | Файлы | Сложность |
|-----|-----------|-------|-----------|
| 1.1 | POST /api/v1/analytics/messages endpoint | `backend/api/routes/analytics.py`, `backend/schemas/analytics.py` | Средняя |
| 1.2 | ChatAnalyticsService.store_message() | `backend/services/chat_analytics_service.py` | Средняя |
| 1.3 | MessageCollectorHandler в frontend | `frontend/bot_handlers.py`, `frontend/bot.py` | Средняя |
| 1.4 | backend_client.analytics_store_message() | `frontend/backend_client.py` | Низкая |
| 1.5 | CRUD для ChatAnalyticsConfig | `backend/api/routes/analytics.py`, `backend/services/chat_analytics_service.py` | Средняя |
| 1.6 | Тесты: unit для store_message, integration для endpoint | `tests/test_analytics_*.py` | Средняя |

**Milestone:** Бот собирает сообщения из групп, видны в БД.

### Фаза 2: Импорт истории (2 дня)

**Цель:** Админ может загрузить экспорт чата и заполнить историю.

| Шаг | Что делать | Файлы | Сложность |
|-----|-----------|-------|-----------|
| 2.1 | Парсер Telegram JSON export (расширенный) | `shared/document_loaders/chat_history_parser.py` | Средняя |
| 2.2 | Парсер Telegram HTML export | Там же | Средняя |
| 2.3 | Парсер TXT/CSV | Там же | Низкая |
| 2.4 | HistoryImportService с дедупликацией | `backend/services/history_import_service.py` | Средняя |
| 2.5 | POST /api/v1/analytics/import endpoint | `backend/api/routes/analytics.py` | Средняя |
| 2.6 | Bot UI: импорт файлов через админ-панель | `frontend/bot_callbacks.py`, `frontend/templates/buttons.py` | Средняя |

**Milestone:** Можно загрузить экспорт чата и видеть историю в БД.

### Фаза 3: Поиск и Q&A (3-4 дня)

**Цель:** Семантический поиск по истории чата и Q&A режим.

| Шаг | Что делать | Файлы | Сложность |
|-----|-----------|-------|-----------|
| 3.1 | ChatAnalyticsRAG: embedding + FAISS | `shared/chat_analytics_rag.py` | Высокая |
| 3.2 | Batch embedding при импорте и при сборе | `backend/services/chat_analytics_service.py` | Средняя |
| 3.3 | ChatSearchService: hybrid search | `backend/services/chat_search_service.py` | Высокая |
| 3.4 | POST /api/v1/analytics/search endpoint | `backend/api/routes/analytics.py` | Средняя |
| 3.5 | POST /api/v1/analytics/qa endpoint | `backend/api/routes/analytics.py` | Средняя |
| 3.6 | Bot commands: /search_chat, /ask_chat | `frontend/bot_handlers.py`, `frontend/bot.py` | Средняя |
| 3.7 | Тесты: поиск по тестовым данным, Q&A quality | `tests/test_chat_search.py` | Средняя |

**Milestone:** Пользователи могут искать по истории чата и задавать вопросы.

### Фаза 4: Кластеризация и дайджесты (4-5 дней)

**Цель:** Автоматическая кластеризация тем и генерация дайджестов.

| Шаг | Что делать | Файлы | Сложность |
|-----|-----------|-------|-----------|
| 4.1 | ThemeClusteringService: preprocessing + HDBSCAN | `backend/services/theme_clustering_service.py` | Высокая |
| 4.2 | LLM theme extraction | Там же | Средняя |
| 4.3 | DigestGeneratorService: формирование дайджеста | `backend/services/digest_generator_service.py` | Средняя |
| 4.4 | POST /api/v1/analytics/digests/generate | `backend/api/routes/analytics.py` | Средняя |
| 4.5 | GET /api/v1/analytics/digests/* | Там же | Низкая |
| 4.6 | Bot command: /digest, кнопка "Сгенерировать сейчас" | `frontend/bot_handlers.py`, `frontend/bot_callbacks.py` | Средняя |
| 4.7 | Форматирование дайджеста для Telegram (HTML/Markdown) | `backend/services/digest_generator_service.py` | Средняя |
| 4.8 | Тесты: кластеризация на синтетических данных | `tests/test_clustering.py` | Средняя |

**Milestone:** Можно вручную сгенерировать дайджест для чата.

### Фаза 5: Расписание и автоматическая доставка (2-3 дня)

**Цель:** Дайджесты генерируются и отправляются автоматически по расписанию.

| Шаг | Что делать | Файлы | Сложность |
|-----|-----------|-------|-----------|
| 5.1 | SchedulerService с APScheduler | `backend/services/scheduler_service.py` | Высокая |
| 5.2 | Интеграция scheduler в backend startup | `backend/app.py` | Низкая |
| 5.3 | Redis Pub/Sub: доставка уведомлений боту | `backend/services/scheduler_service.py`, `frontend/bot.py` | Средняя |
| 5.4 | Бот: Redis subscriber + отправка дайджеста | `frontend/bot.py`, `frontend/bot_handlers.py` | Средняя |
| 5.5 | Admin UI: настройка расписания | `frontend/bot_callbacks.py`, `frontend/templates/buttons.py` | Средняя |
| 5.6 | PUT /api/v1/analytics/configs/{chat_id} с обновлением scheduler | `backend/api/routes/analytics.py` | Средняя |

**Milestone:** Дайджесты генерируются и отправляются по cron.

### Фаза 6: Админ-панель и полировка (2-3 дня)

**Цель:** Полноценная админ-панель для управления аналитикой.

| Шаг | Что делать | Файлы | Сложность |
|-----|-----------|-------|-----------|
| 6.1 | Полный UI: настройка чатов, просмотр дайджестов, статистика | `frontend/bot_callbacks.py`, `frontend/templates/buttons.py` | Средняя |
| 6.2 | GET /api/v1/analytics/stats/{chat_id} | `backend/api/routes/analytics.py` | Низкая |
| 6.3 | Управление permissions (кто может использовать /search_chat) | `frontend/bot_handlers.py` | Низкая |
| 6.4 | Retention policy: автоочистка старых сообщений | `backend/services/chat_analytics_service.py` | Низкая |
| 6.5 | Мониторинг: логирование, метрики сбора | Через logger (уже есть shared/logging_config.py) | Низкая |
| 6.6 | End-to-end тесты | `tests/test_analytics_e2e.py` | Средняя |

**Milestone:** Feature complete. Готово к review и QA.

### Суммарная оценка

| Фаза | Дни | Зависимости |
|------|-----|-------------|
| 0. Подготовка | 1-2 | -- |
| 1. Сбор сообщений | 2-3 | Фаза 0 |
| 2. Импорт истории | 2 | Фаза 0 |
| 3. Поиск и Q&A | 3-4 | Фаза 1 |
| 4. Кластеризация и дайджесты | 4-5 | Фаза 3 |
| 5. Расписание и доставка | 2-3 | Фаза 4 |
| 6. Админ-панель и полировка | 2-3 | Фазы 1-5 |
| **Итого** | **16-22 дня** | |

Фазы 1 и 2 могут идти параллельно. Фаза 3 зависит от Фазы 1 (нужны реальные данные для поиска).

---

## Приложение A: Структура файлов (новые и измененные)

```
telegram_bot_ai/
  shared/
    database.py                          # ИЗМЕНЕНИЕ: +6 новых ORM моделей
    config.py                            # ИЗМЕНЕНИЕ: +ANALYTICS_* переменные
    chat_analytics_rag.py                # НОВЫЙ: FAISS + search для chat messages
    document_loaders/
      chat_history_parser.py             # НОВЫЙ: расширенные парсеры истории

  backend/
    app.py                               # ИЗМЕНЕНИЕ: +analytics router, +scheduler startup
    api/routes/
      analytics.py                       # НОВЫЙ: REST API для аналитики
    schemas/
      analytics.py                       # НОВЫЙ: Pydantic модели
    services/
      chat_analytics_service.py          # НОВЫЙ: оркестрация
      theme_clustering_service.py        # НОВЫЙ: кластеризация
      digest_generator_service.py        # НОВЫЙ: генерация дайджестов
      chat_search_service.py             # НОВЫЙ: поиск + Q&A
      history_import_service.py          # НОВЫЙ: импорт истории
      scheduler_service.py               # НОВЫЙ: APScheduler

  frontend/
    bot.py                               # ИЗМЕНЕНИЕ: +MessageCollectorHandler, +Redis subscriber
    bot_handlers.py                      # ИЗМЕНЕНИЕ: +message_collector, +/digest, +/search_chat, +/ask_chat
    bot_callbacks.py                     # ИЗМЕНЕНИЕ: +analytics admin UI callbacks
    backend_client.py                    # ИЗМЕНЕНИЕ: +analytics_* methods
    templates/
      buttons.py                         # ИЗМЕНЕНИЕ: +analytics menu buttons

  tests/
    test_chat_analytics.py               # НОВЫЙ
    test_chat_search.py                  # НОВЫЙ
    test_clustering.py                   # НОВЫЙ
    test_history_parser.py               # НОВЫЙ

  requirements.txt                       # ИЗМЕНЕНИЕ: +apscheduler, +hdbscan, +scikit-learn
  env.template                           # ИЗМЕНЕНИЕ: +ANALYTICS_* vars
  docker-compose.yml                     # БЕЗ ИЗМЕНЕНИЙ (Redis уже есть)
```

## Приложение B: Конфигурационные переменные

```env
# Chat Analytics
ANALYTICS_ENABLED=true                    # Глобальный вкл/выкл
ANALYTICS_MIN_TEXT_LENGTH=10              # Минимальная длина текста для embedding
ANALYTICS_EMBEDDING_BATCH_SIZE=64         # Batch size для embedding
ANALYTICS_MAX_THEMES=15                   # Максимум тем в дайджесте
ANALYTICS_CLUSTER_METHOD=hdbscan          # hdbscan или agglomerative
ANALYTICS_CLUSTER_MIN_SIZE=5              # Мин. размер кластера
ANALYTICS_DIGEST_MAX_MESSAGES=10000       # Макс. сообщений для одного дайджеста
ANALYTICS_RETENTION_DAYS=365              # Сколько дней хранить сообщения (0=бессрочно)
```

## Приложение C: Промпты для LLM

### Промпт: Извлечение темы из кластера

```
You are analyzing a cluster of chat messages from a Telegram group discussion.
Your task is to identify the main theme of this cluster.

Messages (from the cluster):
---
{messages_formatted}
---

Instructions:
1. Identify the main topic being discussed
2. Write a concise title (max 10 words)
3. Write a summary (2-6 sentences) describing the key points
4. Pick a single emoji that represents this theme
5. List any decisions that were made
6. List any unresolved questions

IMPORTANT:
- Respond in the SAME LANGUAGE as the messages
- Be factual -- do NOT add information not present in the messages
- Use neutral tone
- Return ONLY valid JSON

JSON format:
{
  "emoji": "<emoji>",
  "title": "<title>",
  "summary": "<summary>",
  "key_decisions": ["<decision>", ...],
  "unresolved_questions": ["<question>", ...]
}
```

### Промпт: Общая сводка дайджеста

```
You are creating a summary block for a chat digest covering the period {period_start} to {period_end}.

The following themes were identified:
---
{themes_formatted}
---

Statistics:
- Total messages: {total_messages}
- Unique participants: {unique_participants}
- Active topics/threads: {active_threads}

Create a brief overall summary with:
1. Total themes discussed
2. Key decisions made (across all themes)
3. Unresolved questions (across all themes)
4. One-sentence period summary

IMPORTANT:
- Respond in the SAME LANGUAGE as the theme titles/summaries
- Be concise and factual
- Use neutral tone

Return ONLY valid JSON:
{
  "period_summary": "<one sentence>",
  "key_decisions": ["<decision>", ...],
  "unresolved_questions": ["<question>", ...],
  "theme_count": <int>
}
```

### Промпт: Q&A по истории чата

```
You are answering a question based ONLY on the provided chat message history.

Question: {question}

Relevant messages (sorted by relevance):
---
{messages_with_metadata}
---

Instructions:
1. Answer ONLY based on the provided messages
2. Include references to specific messages using their links: [source](link)
3. If the information is insufficient to answer the question, explicitly say so
4. Use neutral, factual tone
5. Respond in the same language as the question

Format your response as plain text with [source](link) references inline.
If you cannot answer, respond: "Недостаточно информации в истории чата для ответа на этот вопрос."
```
