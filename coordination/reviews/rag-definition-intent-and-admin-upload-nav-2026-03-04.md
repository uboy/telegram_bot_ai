# Review Report: RAG Definition Intent and Admin Upload Navigation

- Date: 2026-03-04
- Reviewer: codex-review
- Task: RAGNAV-001
- Verdict: PASS

## Scope Reviewed
- `backend/api/routes/rag.py`
- `frontend/templates/buttons.py`
- `frontend/bot_callbacks.py`
- Tests:
  - `tests/test_rag_query_definition_intent.py`
  - `tests/test_buttons_admin_menu.py`

## Findings
- Added `DEFINITION` retrieval intent and ranking boosts for definition-like queries.
- Added explicit boost for `пункт N` references in user query.
- Added SQL keyword fallback for definition/point queries to recover relevant chunks when semantic candidates miss exact clauses.
- Kept behavior backward-compatible for existing intents (`HOWTO`, `TROUBLE`, `GENERAL`).
- Removed duplicate global upload button from admin menu.
- Kept `admin_upload` callback as compatibility redirect for stale messages.

## Verification
- `python -m py_compile backend/api/routes/rag.py frontend/templates/buttons.py frontend/bot_callbacks.py tests/test_rag_query_definition_intent.py tests/test_buttons_admin_menu.py` -> PASS
- `.venv\Scripts\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_buttons_admin_menu.py tests/test_bot_document_upload.py tests/test_bot_text_ai_mode.py` -> PASS (`13 passed`)
- `.venv\Scripts\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_buttons_admin_menu.py tests/test_bot_document_upload.py tests/test_bot_text_ai_mode.py` -> PASS (`14 passed`)
- `python scripts/scan_secrets.py` -> PASS

## Residual Risk
- Intent heuristics are lexical and may require tuning for edge cases in mixed-language queries.
