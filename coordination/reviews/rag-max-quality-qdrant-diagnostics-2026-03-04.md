# Review Report: RAG Max Quality v1 (Qdrant + Diagnostics)

- Date: 2026-03-04
- Reviewer: codex-review
- Task: RAGMAX-004
- Verdict: PASS

## Scope Reviewed
- `shared/qdrant_backend.py`
- `shared/rag_system.py`
- `shared/config.py`
- `env.template`
- `docker-compose.yml`
- `backend/api/routes/rag.py`
- `backend/schemas/rag.py`
- `shared/database.py`
- Tests:
  - `tests/test_qdrant_backend.py`
  - `tests/test_rag_diagnostics.py`
  - `tests/test_rag_query_definition_intent.py`

## Findings
- Introduced external Qdrant adapter for dense retrieval with collection management, search, and filtered deletes.
- Optimized Qdrant adapter: collection vector-size is cached to avoid repeated `GET /collections/{name}` calls on every chunk upsert.
- Added `RAG_BACKEND` switch (`qdrant|legacy`) with Qdrant runtime config and docker-compose service wiring.
- Integrated RAG dense channel with Qdrant while preserving lexical BM25 channel and legacy rollback path.
- Added retrieval diagnostics persistence (`retrieval_query_logs`, `retrieval_candidate_logs`) with `request_id`.
- `/api/v1/rag/query` now returns `request_id` in all response paths (success/empty/error).
- Added diagnostics API: `GET /api/v1/rag/diagnostics/{request_id}`.
- Added regression tests for diagnostics flow and Qdrant adapter behavior.

## Verification
- `python -m py_compile backend/api/routes/rag.py tests/test_rag_diagnostics.py tests/test_qdrant_backend.py` -> PASS
- `python -m py_compile shared/qdrant_backend.py shared/rag_system.py shared/config.py shared/database.py backend/api/routes/rag.py backend/schemas/rag.py tests/test_qdrant_backend.py tests/test_rag_diagnostics.py` -> PASS
- `.venv\Scripts\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_rag_diagnostics.py tests/test_qdrant_backend.py` -> PASS
- `.venv\Scripts\python.exe -m pytest -q tests/test_qdrant_backend.py tests/test_rag_diagnostics.py tests/test_rag_query_definition_intent.py` -> PASS
- `.venv\Scripts\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_rag_diagnostics.py tests/test_qdrant_backend.py tests/test_bot_text_ai_mode.py tests/test_bot_document_upload.py tests/test_buttons_admin_menu.py` -> PASS
- `.venv\Scripts\python.exe scripts/rag_api_smoke_test.py --help` -> PASS
- `python scripts/scan_secrets.py` -> PASS
- `python scripts/ci_policy_gate.py --working-tree` -> PASS

## Residual Risk
- Sparse retrieval still runs in-process BM25; full external sparse migration and benchmark gate automation remain for next increment.
- Qdrant bootstrap currently syncs lazily on search; very large KBs may need dedicated background sync tuning.
