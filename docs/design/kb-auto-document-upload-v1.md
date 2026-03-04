# Design: KB Auto Document Upload v1

Date: 2026-03-04  
Type: Behavior improvement (admin upload UX + reliability)

## Problem
- Admin upload flow required manual type selection before document upload.
- Existing `handle_document` path did not run full ingestion/reporting.
- Pending-file flow had stale callback integration.

## Goals
- Remove explicit preselection of file type for document uploads.
- Support one or multiple document uploads in a single flow.
- Validate Telegram file-size limits before ingestion.
- Return per-file success/failure report after processing.

## Scope
- `frontend/bot_callbacks.py`:
  - `kb_upload` now opens auto-detect mode with direct instructions.
  - legacy `upload_type:*` callbacks redirect to auto-detect messaging.
  - pending docs after KB selection are processed as a batch via handler helper.
- `frontend/bot_handlers.py`:
  - auto type inference by extension/mime,
  - pending queue for uploads before KB selection,
  - media-group batch accumulation,
  - backend ingestion job polling and consolidated report generation.
  - report `total_chunks` is resolved from KB import log after job completion (async launch response always has `total_chunks=0`).
- `backend/services/ingestion_service.py`:
  - fallback inference of `file_type` from `file_name` if API client omitted type.

## Telegram Limits
- Upload pipeline validates file size against `get_telegram_file_max_bytes()` before download/processing.
- Oversized files are not ingested and are reported as failed with limit details.

## Verification
- `python -m py_compile frontend/bot_handlers.py frontend/bot_callbacks.py backend/services/ingestion_service.py tests/test_bot_document_upload.py`
- `.venv\Scripts\python.exe -m pytest -q tests/test_bot_document_upload.py tests/test_bot_text_ai_mode.py`
- Extra checks:
  - `test_ingest_single_document_payload_calls_backend_ingestion`
  - `test_ingest_single_document_payload_reports_missing_job_id`

## Risks
- Processing many large files concurrently can increase backend load.
- Mitigation: bounded concurrency (`DOCUMENT_JOB_MAX_PARALLEL`) in bot upload pipeline.

## Rollback
- Revert upload-flow changes in:
  - `frontend/bot_callbacks.py`
  - `frontend/bot_handlers.py`
  - `backend/services/ingestion_service.py`
- Revert `tests/test_bot_document_upload.py` if behavior rollback is required.
