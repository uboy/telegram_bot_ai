# Review Report: Auto-Detect Multi-Document Upload

- Date: 2026-03-04
- Reviewer: codex-review
- Task: DOCUPL-002
- Verdict: PASS

## Scope Reviewed
- `frontend/bot_handlers.py`
- `frontend/bot_callbacks.py`
- `backend/services/ingestion_service.py`
- `tests/test_bot_document_upload.py`
- spec/design/usage/traceability updates

## Findings
- `kb_upload` no longer forces type preselection and now guides admin to send files directly.
- Document handler now:
  - auto-detects file types,
  - validates Telegram file-size limits,
  - supports pending queues and media-group batch upload,
  - polls ingestion jobs and sends per-file success/failure report.
  - resolves actual `total_chunks` from import log after job completion (instead of async launch response placeholder).
- Backend ingestion hardened with file-type inference from filename when type omitted.
- UI labels cleaned up where mojibake `??` appeared in KB settings/code buttons.
- Automated tests cover:
  - type inference,
  - report formatting with failures,
  - pending queue accumulation,
  - batch reporting flow,
  - explicit ingestion call + missing `job_id` fallback handling.

## Verification
- `python -m py_compile frontend/bot_handlers.py frontend/bot_callbacks.py backend/services/ingestion_service.py tests/test_bot_document_upload.py` -> PASS
- `.venv\Scripts\python.exe -m pytest -q tests/test_bot_document_upload.py` -> PASS (`6 passed`)
- `.venv\Scripts\python.exe -m pytest -q tests/test_bot_document_upload.py tests/test_bot_text_ai_mode.py` -> PASS (`11 passed`)
- `python scripts/scan_secrets.py` -> PASS

## Residual Risk
- No end-to-end Telegram integration test for multi-file media-group timing; behavior validated at handler level with unit tests.
