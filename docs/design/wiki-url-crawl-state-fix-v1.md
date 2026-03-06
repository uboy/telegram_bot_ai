# Design: Wiki URL Crawl State Fix v1

Date: 2026-03-06  
Type: Bugfix (Telegram admin wiki ingestion flow)

## Problem
- Admin callback `kb_wiki_crawl:<kb_id>` sets `context.user_data['state'] = 'waiting_wiki_root'` and asks user to send wiki root URL.
- Text handler had no `waiting_wiki_root` branch, so URL input did not trigger wiki ingestion.
- Result: "Собрать вики по URL" appeared available in UI but did not execute backend crawl.

## Root Cause
- State machine mismatch between callback layer and text handler layer.
- Callback assigns `waiting_wiki_root`; `handle_text` missed corresponding branch.

## Solution
- Add `waiting_wiki_root` branch in `frontend/bot_handlers.py::handle_text`:
  - validate selected KB id from `kb_id_for_wiki`,
  - validate URL starts with `http://` or `https://`,
  - call `backend_client.ingest_wiki_crawl(kb_id, url, telegram_id, username)`,
  - return explicit success stats (`deleted/pages/chunks/wiki_root`) or error,
  - clear temporary wiki state (`state`, `kb_id_for_wiki`) after processing.

## Scope
- Runtime code: `frontend/bot_handlers.py`.
- Regression coverage: `tests/test_bot_text_ai_mode.py`.
- Spec/docs/traceability updates for acceptance criteria consistency.
- No API contract or database schema changes.

## Verification
- `python -m py_compile frontend/bot_handlers.py tests/test_bot_text_ai_mode.py`
- `.venv\Scripts\python.exe -m pytest -q tests/test_bot_text_ai_mode.py tests/test_bot_document_upload.py`
- `python scripts/scan_secrets.py`

## Risks and Mitigations
- Risk: state leakage can affect later commands.
  - Mitigation: always clear `state` and `kb_id_for_wiki` in completion/failure path.
- Risk: broader text-routing regressions in `handle_text`.
  - Mitigation: keep change isolated to one explicit `elif` branch and run neighboring bot-flow tests.

## Rollback
- Revert `waiting_wiki_root` branch in `frontend/bot_handlers.py`.
- Revert regression test and related docs if rolling back behavior.
