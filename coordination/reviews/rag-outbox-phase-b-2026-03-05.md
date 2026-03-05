# Review Report: RAG Outbox Consumer + Drift Audit (Phase B)

- Date: 2026-03-05
- Reviewer: codex-review
- Verdict: PASS

## Scope reviewed

1. `backend/services/index_outbox_worker.py`
2. `backend/app.py`
3. `shared/qdrant_backend.py`
4. `backend/api/routes/rag.py`
5. `backend/schemas/rag.py`
6. `shared/config.py`
7. `tests/test_index_outbox_worker.py`
8. `tests/test_rag_diagnostics.py`
9. `tests/test_qdrant_backend.py`
10. `SPEC.md`
11. `docs/REQUIREMENTS_TRACEABILITY.md`
12. `docs/design/rag-generalized-architecture-v2.md`
13. `docs/OPERATIONS.md`
14. `docs/API_REFERENCE.md`
15. `docs/CONFIGURATION.md`
16. `docs/USAGE.md`
17. `env.template`

## MUST-FIX findings

None.

## SHOULD-FIX findings

1. Add integration test with real DB session + test Qdrant container to validate full `ingest -> outbox -> worker -> qdrant` path.
2. Add explicit metrics endpoint for outbox lag/dead-letter counters (currently available only via DB/status logs).
3. Consider moving FastAPI startup hooks to lifespan API (current `on_event` is deprecated warning).

## Verification

1. `python -m py_compile backend/services/index_outbox_worker.py backend/api/routes/rag.py backend/schemas/rag.py shared/qdrant_backend.py tests/test_qdrant_backend.py tests/test_rag_diagnostics.py tests/test_index_outbox_worker.py` -> PASS
2. `$env:MYSQL_URL=''; $env:DB_PATH='data/test_bot_database.db'; .venv\Scripts\python.exe -m pytest -q tests/test_qdrant_backend.py tests/test_rag_diagnostics.py tests/test_index_outbox_worker.py` -> PASS (`12 passed`)
3. `$env:MYSQL_URL=''; $env:DB_PATH='data/test_bot_database.db'; .venv\Scripts\python.exe -m pytest -q tests/test_api_routes_contract.py tests/test_security_api_key.py tests/test_ingestion_outbox.py tests/test_index_outbox_service.py tests/test_indexing_jobs_lifecycle.py` -> PASS (`12 passed`)
4. `python scripts/scan_secrets.py` -> PASS
5. `python scripts/ci_policy_gate.py --working-tree` -> PASS

## Security review

1. New worker does not introduce credentials or external secret storage.
2. Qdrant operations use existing configured endpoint and API-key handling.
3. Retry/dead-letter errors are truncated and stored without sensitive payload expansion.

## Residual risk

1. Drift audit uses count-level comparison and does not yet include point-ID sampling for deep mismatch diagnostics.
2. Worker runs in-process thread; crash-loop resilience is tied to backend process lifecycle.
