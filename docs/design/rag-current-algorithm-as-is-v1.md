# Design: Текущий RAG-алгоритм (AS-IS, implementation snapshot)

Date: 2026-03-06
Owner: codex
Status: рабочее описание текущей реализации перед следующими изменениями

## 1) Назначение документа

Этот документ фиксирует фактическую реализацию RAG в репозитории на текущий момент:
1. Какие технологии и компоненты используются.
2. Как обрабатываются входные данные при ingestion.
3. Как обрабатывается пользовательский запрос в retrieval/generation.
4. Как устроено хранение, синхронизация индекса и диагностика.

Источник истины: код в `backend/*`, `shared/*`, конфиг в `shared/config.py`, схемы в `shared/database.py`.

## 2) Технологический стек RAG

1. API и orchestration:
- FastAPI (`backend/app.py`, `backend/api/routes/rag.py`, `backend/api/routes/ingestion.py`).

2. LLM и prompting:
- `shared.ai_providers.ai_manager` для вызова модели.
- Prompt builder: `shared.utils.create_prompt_with_language`.
- Пост-обработка безопасности: `shared.rag_safety` (`strip_unknown_citations`, `strip_untrusted_urls`, `sanitize_commands_in_answer`).

3. Retrieval:
- Dense embeddings: `sentence-transformers` (`SentenceTransformer`).
- Optional rerank: `CrossEncoder`.
- Legacy dense index: FAISS (`IndexFlatIP` + L2 normalization для cosine-like поиска).
- Sparse/lexical channel: in-memory BM25 (собственная реализация в `shared/rag_system.py`).
- Hybrid fusion: RRF (Reciprocal Rank Fusion).

4. Хранение:
- SQLAlchemy + SQLite/MySQL (`shared/database.py`).
- Основные таблицы RAG: `knowledge_bases`, `documents`, `document_versions`, `knowledge_chunks`, `knowledge_import_logs`.
- Диагностика/операции: `retrieval_query_logs`, `retrieval_candidate_logs`, `index_outbox_events`, `index_sync_audit`, `rag_eval_runs`, `rag_eval_results`, `retention_deletion_audit`.

5. Dense backend v3:
- `RAG_BACKEND=legacy|qdrant`.
- Для `qdrant`: REST-адаптер `shared/qdrant_backend.py`.
- Для `legacy`: FAISS в процессе приложения.

## 3) Обработка входных данных (Ingestion)

## 3.1 API ingestion и async jobs

Ingestion идет через `backend/api/routes/ingestion.py`:
1. `POST /api/v1/ingestion/document`
2. `POST /api/v1/ingestion/web`
3. `POST /api/v1/ingestion/wiki-*`
4. `POST /api/v1/ingestion/image`
5. `POST /api/v1/ingestion/code-*`

Все тяжелые операции запускаются асинхронно через `IndexingService`:
1. создается `Job` (`status=pending`),
2. запускается background thread,
3. статус отслеживается через `GET /api/v1/jobs/{id}`.

## 3.2 Источники и загрузчики

`DocumentLoaderManager` (`shared/document_loaders/__init__.py`) роутит по типам:
1. PDF (`PDFLoader`)
2. Markdown (`MarkdownLoader`)
3. Word (`WordLoader`)
4. Excel (`ExcelLoader`)
5. Text (`TextLoader`)
6. Code (`CodeLoader`)
7. Web (`WebLoader`)
8. Chat export (`ChatLoader`)
9. Image (`ImageLoader`, фактический OCR/vision шаг отдельно в `IngestionService.ingest_image`)

## 3.3 Chunking стратегия

Базовые функции чанкинга в `shared/document_loaders/chunking.py`:
1. `split_text_into_chunks` (fixed-size + overlap).
2. `split_text_structurally` (абзацы/списки/кодовые блоки).
3. `split_markdown_section_into_chunks` (структурный markdown chunking).
4. `split_code_into_chunks` (попытка делить по классам/функциям, fallback на fixed).

Настройки по умолчанию:
1. Глобальные: `RAG_CHUNK_SIZE`, `RAG_CHUNK_OVERLAP` (`shared/config.py`).
2. Пер-KB: `kb.settings.chunking` (`shared/kb_settings.py`):
- web/wiki/markdown -> `full` (по умолчанию),
- text -> `fixed`,
- code -> `file` (в сервисе преобразуется в `full`).

## 3.4 Нормализация, версионирование и запись

`IngestionService` (`backend/services/ingestion_service.py`) делает общий pipeline:
1. Определяет настройки KB.
2. Загружает и чанкует контент.
3. Классифицирует документ эвристикой (`shared/rag_pipeline/classifier.py`).
4. Определяет язык (`detect_language`).
5. Считает `doc_hash` (SHA-256).
6. Делает upsert в `documents` + `document_versions`.
7. Помечает старые чанки источника через `rag_system.delete_chunks_by_source_exact(..., soft_delete=True)`.
8. Готовит metadata (document_class, language, doc_hash/doc_version/source_updated_at, section/doc markers, code_lang и т.д.).
9. Пишет чанки батчем через `rag_system.add_chunks_batch`.
10. Пишет `knowledge_import_logs`.
11. Создает outbox-событие `UPSERT` в `index_outbox_events`.

Отдельные ветки:
1. ZIP: обрабатывается файл-за-файлом, source_path = внутренний путь в архиве.
2. Codebase path/git: поддержка инкрементального обновления по hash.
3. Image: через `image_processor.process_image_for_rag`, затем `add_chunk`.
4. Wiki crawl/git/zip: загрузка через `shared/wiki_scraper.py`, `shared/wiki_git_loader.py`.

## 4) Хранение и индексирование

## 4.1 SQL как source of truth

Primary truth хранится в SQL (`knowledge_chunks`, `documents`, `document_versions`).
В `knowledge_chunks` важны поля:
1. `content`
2. `chunk_metadata` (JSON string)
3. `embedding` (JSON vector)
4. `source_type`, `source_path`
5. `document_id`, `version`
6. `is_deleted`

## 4.2 Dense index backend

`RAGSystem` в `shared/rag_system.py`:
1. Загружает encoder/reranker при старте (с `HF_HOME` cache).
2. Поддерживает backend switch:
- `legacy`: FAISS per-KB + global index.
- `qdrant`: dense поиск через REST в Qdrant, lexical канал остается локальным BM25.

## 4.3 BM25 канал

BM25 индекс строится в памяти на активных чанках:
1. tokenization: regex `\w+`.
2. хранится per-KB (`bm25_index_by_kb`) и общий (`bm25_index_all`).

## 4.4 Outbox worker и консистентность индекса

`IndexOutboxService` + `index_outbox_worker`:
1. ingestion пишет идемпотентные события (`idempotency_key`).
2. worker регулярно claim-ит pending events.
3. операции:
- `UPSERT`: читает релевантные chunk rows и upsert в Qdrant,
- `DELETE_SOURCE`,
- `DELETE_KB`.
4. retries/backoff/dead-letter.
5. drift audit (`index_sync_audit`): сравнение SQL expected vs Qdrant count.
6. retention loop (очистка query/eval/version/audit данных).

## 5) Алгоритм обработки запроса (Query path)

## 5.1 Контракт API запроса

`POST /api/v1/rag/query` (`RAGQuery`):
1. `query` (required)
2. `knowledge_base_id` (optional)
3. `top_k` (optional)
4. filters: `source_types`, `languages`, `path_prefixes`, `date_from`, `date_to`

## 5.2 Retrieval в `rag_system.search`

Шаги алгоритма:
1. Если embeddings недоступны -> `_simple_search` (keyword heuristic scoring по content/title/section/source_path).
2. Иначе:
- lazy `_load_index(...)`,
- embedding запроса (`_get_embedding`),
- определение how-to query (`_is_howto_query`),
- вычисление `candidate_k`.
3. Dense channel:
- qdrant mode: `_qdrant_dense_search`,
- legacy mode: FAISS search по normalized vectors.
4. Sparse channel:
- `_bm25_search(...)` поверх BM25 индекса.
5. Fusion:
- RRF `_rrf_fuse(dense_ranked_keys, bm25_ranked_keys)`.
6. Dedup:
- ключ `(source_path, content[:200])`.
7. Ranking:
- если reranker доступен: cross-encoder score + `source_boost`,
- иначе fallback sort (для how-to отдельные приоритеты по `chunk_kind`/origin).
8. Возврат top_k кандидатов с полями `content`, `metadata`, `source_type`, `source_path`, `distance`, optional `rerank_score`, `origin`.

## 5.3 Route-level orchestration в `backend/api/routes/rag.py`

После `rag_system.search` применяется orchestration-слой API:
1. Парсинг глобальных параметров (`RAG_TOP_K`, `RAG_CONTEXT_LENGTH`, `RAG_MIN_RERANK_SCORE`, `RAG_ORCHESTRATOR_V4`).
2. Загрузка KB settings (`single_page_mode`, `single_page_top_k`, `full_page_context_multiplier`).
3. Применение metadata/date filters из payload.
4. Режимы orchestrator:
- `legacy` (`RAG_ORCHESTRATOR_V4=false`): intent detection, query hints, route-level boosts, keyword fallback.
- `v4` (`RAG_ORCHESTRATOR_V4=true`): intent форсируется в `GENERAL`, route-level boosts/fallback не применяются.
5. Legacy-only функции:
- `detect_intent` (`HOWTO`, `TROUBLE`, `DEFINITION`, `FACTOID`, `GENERAL`),
- `extract_query_hints` (point numbers, fact terms, years, phrases),
- `fetch_keyword_fallback_chunks` через SQL `ILIKE` по `knowledge_chunks`.
6. Anti-hallucination gate:
- если есть rerank_scores и `max(score) < RAG_MIN_RERANK_SCORE` -> пустой ответ.
7. Rank score в route:
- legacy: `apply_boosts(...)` (query-specific эвристики),
- v4: `base_score(...)` без дополнительных query boosts.
8. Выбор документов `select_docs(...)` и final `filtered_results`.

## 5.4 Context construction для LLM

Формирование контекста:
1. `build_context_block(...)` собирает блок с:
- `SOURCE_ID` (если citations enabled),
- `DOC`, `SECTION`, `TYPE`, `LANG`,
- `CONTENT` (truncate по типу чанка).
2. Для `HOWTO` (legacy):
- подтягиваются соседние чанки того же документа (`load_doc_chunks` + `build_context_blocks`).
3. Для `FACTOID` (legacy):
- приоритет top direct evidence chunks.
4. Для остальных:
- top `top_k_for_context` блоков.

## 5.5 Generation и post-processing

1. Prompt: `create_prompt_with_language(query, context_text, task="answer")`.
2. Generation: `ai_manager.query(prompt)`.
3. Safety post-processing:
- `strip_unknown_citations`,
- `strip_untrusted_urls`,
- `sanitize_commands_in_answer`.
4. Response:
- `answer`,
- `sources[]`,
- `request_id`,
- optional `debug_chunks` (если `RAG_DEBUG_RETURN_CHUNKS=true`).

## 5.6 Диагностика

Для каждого запроса сохраняются:
1. `retrieval_query_logs` (intent/hints/filters/latency/backend/degraded flags).
2. `retrieval_candidate_logs` (ranked top candidates, origin/channel/fusion/rerank fields).

Доступ:
1. `GET /api/v1/rag/diagnostics/{request_id}`.
2. `orchestrator_mode` читается из `hints_json`.

## 6) Дополнительные контуры эксплуатации

1. Eval orchestration:
- `POST /api/v1/rag/eval/run`,
- `GET /api/v1/rag/eval/{run_id}`,
- suite читается из `tests/rag_eval.yaml` (или `RAG_EVAL_SUITE_FILE`).

2. Retention:
- периодическое удаление старых query logs / candidate logs / eval logs / drift audits / old document versions.

3. Модельная перезагрузка:
- `POST /api/v1/rag/reload-models`.

## 7) Критичные особенности текущей реализации (AS-IS)

1. Retrieval двухуровневый:
- базовый уровень в `rag_system.search` (hybrid dense+sparse+rerank),
- дополнительный route-level orchestration (intent boosts/fallback), который может менять поведение в legacy.

2. `v4` отключает route-level hardcoded boosts/fallback, но не выключает все эвристики в `rag_system` (например, how-to logic и source boost остаются).

3. Chunking в основном символный/структурный; строгого token-aware budget на этапе ingestion нет.

4. Индексы/кэши в памяти сбрасываются и пересобираются lazy после write операций.

5. Для SQLite write path защищен глобальным lock + retry/backoff, что упрощает консистентность, но ограничивает конкурентную запись.

## 8) Назначение этого snapshot

Этот AS-IS snapshot является базой для:
1. следующего цикла ревью слабых мест алгоритма;
2. планирования миграции на более generalized retrieval без overfitting на тестовые вопросы;
3. проверки, что изменения будут измеряться относительно текущей, формально зафиксированной логики.
