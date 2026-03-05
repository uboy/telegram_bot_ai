# Design: RAG Factoid Quality Hardening v1

Date: 2026-03-05  
Owner: codex

## Problem

Observed production-like logs showed:
- metric/factoid questions sometimes return "нет точной информации" despite relevant clause in KB,
- occasional extra answers from stale KB queue items.

## Scope

- Improve factoid retrieval precision for legal/numeric queries.
- Isolate KB query sessions to prevent stale queue leakage.

## Changes

1. Retrieval hint enrichment (`backend/api/routes/rag.py`)
- Add factoid hints: `key_phrases`, `strict_fact_terms`, `metric_query`, `prefer_numeric`.
- Keep existing hints (`point_numbers`, `year_tokens`, `definition_term`).

2. Factoid scoring hardening (`backend/api/routes/rag.py`)
- Boost exact phrase overlap from user query.
- Extra boost for numeric evidence when query expects metric value.
- Add targeted boosts for period/supercomputer power/mechanisms/federal-law style queries.
- Penalize generic chunks with weak overlap.

3. Keyword fallback hardening (`backend/api/routes/rag.py`)
- Add SQL conditions for key phrases and strict terms.
- Filter weak metric candidates lacking overlap with key terms/phrases/year/point.

4. Context packing policy (`backend/api/routes/rag.py`)
- For `FACTOID`, prefer top direct evidence chunks over neighbor expansion to reduce noise.

5. Prompt cleanup (`shared/utils.py`)
- Remove default "Дополнительно" output pattern for RU answer prompt.
- Keep concise direct answer format with explicit metric value requirement when present.

6. KB queue session isolation (`frontend/bot_handlers.py`)
- On explicit re-entry to KB search mode:
  - clear stale `pending_queries` / `pending_query`,
  - clear queued KB items,
  - cancel existing queue worker,
  - increment query session id.
- Queue worker ignores items from older session id.

## Verification

- `python -m py_compile frontend/bot_handlers.py backend/api/routes/rag.py shared/utils.py tests/test_bot_text_ai_mode.py tests/test_rag_query_definition_intent.py`
- `.venv\Scripts\python.exe -m pytest -q tests/test_bot_text_ai_mode.py tests/test_rag_query_definition_intent.py tests/test_rag_diagnostics.py`

## Risks

- Stronger metric filters may under-retrieve very short noisy OCR chunks.
- Queue reset intentionally drops stale pending items when user restarts KB search mode.
