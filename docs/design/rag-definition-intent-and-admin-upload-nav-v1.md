# Design: RAG Definition Intent + Admin Upload Navigation v1

Date: 2026-03-04  
Type: Retrieval quality + UX fix

## Problem 1: Definition Questions Retrieve Generic Mentions
- For queries like "Как определяется разметка данных?", retrieval could select policy mentions instead of explicit definitions from glossary-like fragments.

## Solution 1
- Extend `rag_query` intent detection with `DEFINITION` intent.
- Add ranking boosts for definition signals:
  - lexical markers (`называется`, `определяется`, `представляет собой`, `этап`, `совокупность`),
  - section metadata hints (`определ*`, `термин*`, `глоссар*`),
  - term-pattern hints (`<term> -`, `<term> —`, `<term>:`).
- Add additional boost for queries referencing specific points (`пункт N`) when chunk text/section contains the same point.
- Add keyword fallback retrieval from SQL chunks for definition/point queries:
  - for definitions: include chunks containing exact target term,
  - for point queries: include chunks matching `пункт N` and numeric section markers (`N.`).
- For `DEFINITION`, document selection prioritizes strongest definition-bearing document(s).

## Problem 2: Duplicate Upload Entry Points
- Admin menu had global upload button and KB-local upload button with different flow expectations.

## Solution 2
- Remove global upload button from admin menu.
- Keep upload entry only via selected KB actions.
- Retain `admin_upload` callback as compatibility redirect to KB selection for stale old messages.

## Verification
- `python -m py_compile backend/api/routes/rag.py frontend/templates/buttons.py frontend/bot_callbacks.py tests/test_rag_query_definition_intent.py tests/test_buttons_admin_menu.py`
- `.venv\Scripts\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_buttons_admin_menu.py tests/test_bot_document_upload.py tests/test_bot_text_ai_mode.py`
- `python scripts/scan_secrets.py`

## Tests Added
- `tests/test_rag_query_definition_intent.py`
- `tests/test_buttons_admin_menu.py`
