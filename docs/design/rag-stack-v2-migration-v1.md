# Design: RAG Stack v2 Migration (Retrieval + KB Query UX)

Date: 2026-03-04  
Type: Retrieval quality + bot UX + verification tooling

## Problem
- Users send fact-based questions ("кто", "как часто", "целевой показатель", "на 2030 год"), but retrieval may miss exact clause chunks.
- KB-search UX looked "silent" on long requests; users sent repeated questions while previous answer was still running.
- No lightweight API smoke runner for quick backend RAG sanity checks.

## Scope
- Keep current ingestion matrix (PDF, DOCX, Markdown, web/wiki, code, images, chat exports) and current storage/search core.
- Improve retrieval behavior for factoid/legal/numeric questions.
- Improve Telegram KB-search UX with progress + ordered queue.
- Add backend API smoke script.
## Architecture Decisions
### 1) Retrieval v2 inside existing stack
- Keep current dense + BM25 + rerank architecture.
- Add explicit `FACTOID` intent in `backend/api/routes/rag.py`.
- Extend query hints:
  - `point_numbers` (пункт N),
  - `definition_term`,
  - `fact_terms`,
  - `year_tokens` (e.g., 2030).
- Add factoid-oriented boosts:
  - lexical term hits in chunk text/section metadata,
  - "кто/как часто" markers,
  - numeric/metric markers (`процент`, `млрд`, `ВВП`, etc.),
  - explicit year matches.
- Generalize SQL keyword fallback for factoid/definition/point queries:
  - content + metadata matching by terms, points, years.
- Expand context assembly for `FACTOID` similar to `HOWTO` (neighbor chunks, larger top-k in single-page mode).
### 2) KB Query UX v2 (frontend)
- Add per-user-session FIFO queue for KB questions in `frontend/bot_handlers.py`.
- Add worker that processes queued questions in order.
- Each answer is sent as `reply_to_message_id` to the original user question.
- Add ephemeral progress bar message for long KB queries:
  - show during wait,
  - auto-delete in `finally` after response/error.
- Pending queries collected before KB selection are flushed into queue after `kb_select:*`.
### 3) API smoke verification
- Add `scripts/rag_api_smoke_test.py`:
  - runs against `/api/v1/rag/query`,
- optional `--kb-id`, auto-detect first KB if omitted,
- supports custom JSON cases and `--fail-on-empty`.
## Quality/Regression Strategy
- Retrieval tests:
  - definition chunk preference,
  - point-based clause fallback,
  - factoid fallback (`кто/как часто`),
  - factoid numeric/year target retrieval.
- Bot UX tests:
  - progress lifecycle (shown/deleted),
  - queue order preservation,
  - pending-query flush into queue.
## Out Of Scope (for this iteration)
- Hard migration to external vector DB (Qdrant/Weaviate/Milvus).
- Late-interaction retrievers (e.g., ColBERT) and multi-index federation.
- Dual-write data migration and online backfill orchestration.
## Full Stack Migration Path (next iterations)
1. Introduce retriever abstraction layer (`DenseRetriever`, `HybridRetriever`, `ExternalVectorRetriever`).
2. Add optional Qdrant backend behind feature flag (shadow-read mode first).
3. Add RRF/hybrid calibration with offline eval set.
4. Migrate production read path gradually (canary KBs), then deprecate FAISS path if metrics improve.
## Verification Commands
- `python -m py_compile backend/api/routes/rag.py frontend/bot_handlers.py frontend/bot_callbacks.py shared/utils.py scripts/rag_api_smoke_test.py tests/test_rag_query_definition_intent.py tests/test_bot_text_ai_mode.py`
- `.venv\Scripts\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_bot_text_ai_mode.py tests/test_buttons_admin_menu.py tests/test_bot_document_upload.py`
- `.venv\Scripts\python.exe scripts/rag_api_smoke_test.py --help`
- `python scripts/scan_secrets.py`
- `python scripts/ci_policy_gate.py --working-tree`
## Rollback Plan
- Retrieval behavior rollback: revert `backend/api/routes/rag.py`.
- UX rollback: revert queue/progress changes in `frontend/bot_handlers.py` and `frontend/bot_callbacks.py`.
- Smoke script addition is isolated and can be removed without runtime impact.
