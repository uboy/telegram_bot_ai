# Review Report: Wiki URL Crawl State Fix

- Date: 2026-03-06
- Reviewer: codex-review (self-review fallback; `code-review-qa` subagent unavailable due provider model error)
- Task: WIKIBUG-003
- Verdict: PASS

## Scope Reviewed
- `frontend/bot_handlers.py`
- `tests/test_bot_text_ai_mode.py`
- spec/design/traceability/usage docs for bugfix lifecycle

## Findings
- Root cause confirmed: `frontend/bot_callbacks.py` set `state='waiting_wiki_root'`, but `frontend/bot_handlers.py::handle_text` had no matching branch.
- Fix correctness verified:
  - Added explicit `waiting_wiki_root` handling in text state machine.
  - Calls backend wiki-crawl ingestion via `backend_client.ingest_wiki_crawl`.
  - Returns explicit crawl summary stats to admin.
  - Clears temporary wiki state keys (`state`, `kb_id_for_wiki`) after processing.
- Regression coverage verified:
  - Added `test_handle_text_waiting_wiki_root_ingests_wiki_crawl`.
  - Test reproduces pre-fix failure and verifies backend call, response path, and state reset.

## Verification
- `.venv\Scripts\python.exe -m pytest -q tests/test_bot_text_ai_mode.py -k waiting_wiki_root` -> FAIL (pre-fix expected)
- `.venv\Scripts\python.exe -m pytest -q tests/test_bot_text_ai_mode.py tests/test_bot_document_upload.py` -> PASS (`17 passed`)
- `python -m py_compile frontend/bot_handlers.py tests/test_bot_text_ai_mode.py` -> PASS
- `python scripts/scan_secrets.py` -> PASS

## Risks
- Existing `wiki_git_load` / `wiki_zip_load` callbacks still depend on `wiki_urls` context mapping that is not produced in current flow.
- This fix intentionally targets only the broken "wiki crawl by URL" path with minimal blast radius.

## Recommendation
- Optional follow-up: unify wiki mode selection flow and add dedicated tests for git/zip callback branches.
