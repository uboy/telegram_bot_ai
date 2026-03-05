# Review Report: RAG Outbox Foundation (Phase A)

- Date: 2026-03-05
- Reviewer: codex-review
- Verdict: PASS

## Scope reviewed

1. `shared/database.py`
2. `shared/index_outbox_service.py`
3. `backend/services/ingestion_service.py`
4. `tests/test_index_outbox_service.py`
5. `tests/test_ingestion_outbox.py`
6. `docs/design/rag-generalized-architecture-v2.md`
7. `SPEC.md`
8. `docs/REQUIREMENTS_TRACEABILITY.md`

## MUST-FIX findings

None.

## SHOULD-FIX findings

1. Add dedicated background worker that consumes outbox and writes to index backend with visibility metrics (`pending_lag_sec`, `retry_rate`).
2. Add integration tests with real DB session and transactional rollback fixtures (current tests are isolated unit-level).
3. Add retention worker implementation for `retention_deletion_audit` in next phase.

## Post-audit corrections (same date)

1. During checklist audit, two miswired outbox invocations were found and fixed in `backend/services/ingestion_service.py`:
- `ingest_web_page` had wrong event payload (`image` fields) and undefined variable usage.
- `ingest_codebase_path` final aggregate event referenced invalid variables from another method.
2. Added regression tests to lock behavior:
- `test_ingest_web_page_emits_web_outbox_event`
- `test_ingest_codebase_path_emits_code_and_codebase_events`
3. Added outbox events for wiki flows (`wiki`, `wiki_git`, `wiki_zip`) to keep coverage complete for non-empty ingest paths.

## Verification

1. `python -m py_compile shared/database.py shared/index_outbox_service.py backend/services/ingestion_service.py tests/test_index_outbox_service.py tests/test_ingestion_outbox.py` -> PASS
2. `$env:MYSQL_URL=''; $env:DB_PATH='data/test_bot_database.db'; .venv\Scripts\python.exe -m pytest -q tests/test_index_outbox_service.py tests/test_ingestion_outbox.py tests/test_rag_diagnostics.py tests/test_ingestion_routes.py tests/test_indexing_jobs_lifecycle.py` -> PASS (`12 passed`)
3. `python scripts/scan_secrets.py` -> PASS
4. `python scripts/ci_policy_gate.py --working-tree` -> PASS
5. `$env:MYSQL_URL=''; $env:DB_PATH='data/test_bot_database.db'; .venv\Scripts\python.exe -m pytest -q tests/test_bot_text_ai_mode.py tests/test_rag_query_definition_intent.py tests/test_bot_document_upload.py` -> PASS (`22 passed`)

## Security review

1. No secrets or tokens added to source.
2. Outbox payload is metadata-level and does not include credentials.
3. Error logging uses bounded message length and does not expose auth material.

## Residual risk

1. Outbox consumer is not yet connected to retrieval backend write path in this phase.
2. Large-volume load behavior for outbox backlog needs operational tuning in next iteration.
