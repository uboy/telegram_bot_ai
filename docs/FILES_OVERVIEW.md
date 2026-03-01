# Обзор файлов системы RAG

## 📁 Файлы, отвечающие за загрузку данных

### Основные загрузчики документов
- **`shared/document_loaders/`** — модульная система загрузчиков:
  - **`base.py`** — базовый абстрактный класс `DocumentLoader`
  - **`markdown_loader.py`** — загрузка Markdown файлов с сохранением структуры (заголовки, code blocks)
  - **`word_loader.py`** — загрузка Word документов (.docx)
  - **`pdf_loader.py`** — загрузка PDF файлов
  - **`excel_loader.py`** — загрузка Excel файлов (.xlsx, .xls)
  - **`web_loader.py`** — загрузка веб-страниц (HTML)
  - **`text_loader.py`** — загрузка обычных текстовых файлов
  - **`image_loader.py`** — загрузка изображений

- **`shared/document_loaders.py`** — реэкспорт всех загрузчиков и менеджер `document_loader_manager`

### Загрузка вики
- **`shared/wiki_git_loader.py`** — загрузка вики из Git репозитория и ZIP архивов
  - `load_wiki_from_git()` — клонирование репозитория и загрузка
  - `load_wiki_from_zip()` — загрузка из ZIP архива
  - `_restore_wiki_url_from_path()` — восстановление URL страниц вики из пути файла

- **`shared/wiki_scraper.py`** — скрапинг вики через HTTP (рекурсивный обход страниц)

### Сервис загрузки (Backend API)
- **`backend/services/ingestion_service.py`** — основной сервис загрузки документов
  - `ingest_document_or_archive()` — загрузка документов и архивов
  - `ingest_web_page()` — загрузка веб-страниц
  - `ingest_wiki_crawl()` — загрузка вики через скрапинг
  - `ingest_wiki_git()` — загрузка вики из Git
  - `ingest_wiki_zip()` — загрузка вики из ZIP
  - `ingest_image()` — обработка изображений

---

## 📄 Файлы, отвечающие за разбор файлов

### Парсинг и обработка
- **`shared/document_loaders/markdown_loader.py`**
  - Извлечение заголовков (H1-H6)
  - Сохранение code blocks
  - Очистка Markdown разметки

- **`shared/document_loaders/web_loader.py`**
  - Парсинг HTML через BeautifulSoup
  - Удаление навигационных элементов
  - Извлечение заголовка страницы (h1, title)

- **`shared/document_loaders/word_loader.py`**
  - Парсинг .docx через python-docx
  - Определение заголовков по стилям

- **`shared/document_loaders/pdf_loader.py`**
  - Извлечение текста из PDF через PyPDF2
  - Обработка постранично

- **`shared/image_processor.py`** — обработка изображений (OCR, описание через AI)

---

## ✂️ Файлы, отвечающие за разбитие на чанки

### Чанкинг
- **`shared/document_loaders/chunking.py`** — основной модуль чанкинга:
  - **`split_text_into_chunks()`** — универсальный разбиватель по символам
  - **`split_markdown_section_into_chunks()`** — чанкинг секций Markdown
  - **`split_text_structurally()`** — структурный чанкинг (код, списки, абзацы)

### Логика чанкинга
- **Структурный чанкинг** (`split_text_structurally`):
  - Сохраняет code blocks как атомарные единицы
  - Сохраняет списки (нумерованные/маркированные) целиком
  - Разбивает абзацы по предложениям при необходимости
  - Учитывает `RAG_CHUNK_SIZE` и `RAG_CHUNK_OVERLAP` из конфига

- **Markdown чанкинг** (`split_markdown_section_into_chunks`):
  - Разбивает по секциям (заголовкам)
  - Сохраняет code blocks
  - Добавляет метаданные о секции

---

## 🔍 Файлы, отвечающие за поиск

### RAG система
- **`shared/rag_system.py`** — основная система RAG:
  - **`RAGSystem`** — класс системы поиска
  - **`search()`** — основной метод поиска (dense + keyword + rerank)
  - **`_simple_search()`** — упрощенный keyword поиск
  - **`_get_embedding()`** — генерация эмбеддингов
  - **`_load_index()`** — загрузка FAISS индекса

### Поиск включает:
1. **Dense поиск** (векторный):
   - Использует FAISS для поиска по эмбеддингам
   - Модель: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`

2. **Keyword поиск** (`_simple_search`):
   - Поиск по ключевым словам в контенте
   - Поиск в заголовках (title, section_title) с повышенным весом
   - Поиск в source_path
   - Поддержка точного совпадения фраз

3. **Reranking** (опционально):
   - Использует Cross-Encoder для переранжирования
   - Улучшает релевантность результатов

### API для поиска
- **`backend/api/routes/rag.py`** — REST API для поиска:
  - `POST /api/v1/rag/query` — поиск в базе знаний
  - `POST /api/v1/rag/summary` — сводка/FAQ/инструкция по БЗ
  - `POST /api/v1/rag/reload-models` — перезагрузка моделей RAG

- **`frontend/bot_handlers.py`** — обработка запросов в Telegram боте:
  - `handle_text()` — обработка текстовых запросов пользователей

- **`frontend/backend_client.py`** — HTTP клиент для обращения к backend API

---

## 🗄️ Хранение данных

- **`shared/database.py`** — схема базы данных:
  - `KnowledgeBase` — базы знаний
  - `KnowledgeChunk` — фрагменты знаний (чанки)
  - `KnowledgeImportLog` — журнал загрузок

- **`shared/rag_system.py`**:
  - `add_chunk()` — добавление одного чанка
  - `add_chunks_batch()` — пакетное добавление чанков
  - Индексация в FAISS для быстрого поиска

---

## 📊 Поток данных

```
1. Загрузка документа
   └─> ingestion_service.py
       └─> document_loader_manager (document_loaders.py)
           └─> Соответствующий loader (markdown_loader.py, word_loader.py, etc.)
               └─> chunking.py (разбиение на чанки)
                   └─> rag_system.add_chunks_batch()
                       └─> database.py (сохранение в БД)
                       └─> FAISS индекс (для поиска)

2. Поиск
   └─> bot_handlers.py / rag.py
       └─> rag_system.search()
           ├─> Dense поиск (FAISS)
           ├─> Keyword поиск (_simple_search)
           └─> Reranking (опционально)
```

---

## 🔧 Конфигурация

Настройки чанкинга и поиска находятся в:
- **`shared/config.py`** (или переменные окружения):
  - `RAG_CHUNK_SIZE` — размер чанка (по умолчанию 2000 символов)
  - `RAG_CHUNK_OVERLAP` — перекрытие между чанками (по умолчанию 400)
  - `RAG_TOP_K` — количество результатов поиска
  - `RAG_MAX_CANDIDATES` — количество кандидатов для reranking
  - `RAG_MODEL_NAME` — модель для эмбеддингов
  - `RAG_ENABLE` — включение/выключение RAG

