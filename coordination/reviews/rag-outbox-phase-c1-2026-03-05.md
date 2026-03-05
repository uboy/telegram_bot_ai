# Review Report: RAG Retention + Eval Orchestration (Phase C part-1)

- Date: 2026-03-05
- Reviewer: codex-review
- Verdict: PASS

## Scope reviewed

1. `backend/services/rag_retention_service.py`
2. `backend/services/rag_eval_service.py`
3. `backend/services/index_outbox_worker.py`
4. `backend/api/routes/rag.py`
5. `backend/schemas/rag.py`
6. `shared/config.py`
7. `env.template`
8. `tests/test_rag_retention_service.py`
9. `tests/test_rag_eval_service.py`
10. `tests/test_rag_eval_api.py`
11. `tests/test_api_routes_contract.py`
12. `SPEC.md`
13. `docs/REQUIREMENTS_TRACEABILITY.md`
14. `docs/design/rag-generalized-architecture-v2.md`
15. `docs/API_REFERENCE.md`
16. `docs/CONFIGURATION.md`
17. `docs/OPERATIONS.md`
18. `docs/USAGE.md`

## MUST-FIX findings

None.

## SHOULD-FIX findings

1. Add dedicated integration test with real RAG KB fixture for eval metrics stability (current tests are focused unit/API-level).
2. Add manual admin endpoint to trigger retention run on demand for incident response.
3. Extend eval service to include generation-faithfulness metrics and CI gate wiring.

## Verification

1. `python -m py_compile backend/services/rag_retention_service.py backend/services/rag_eval_service.py backend/services/index_outbox_worker.py backend/api/routes/rag.py backend/schemas/rag.py shared/config.py tests/test_api_routes_contract.py tests/test_rag_eval_api.py tests/test_rag_eval_service.py tests/test_rag_retention_service.py tests/test_index_outbox_worker.py` -> PASS
2. `$env:MYSQL_URL=''; $env:DB_PATH='data/test_bot_database.db'; .venv\Scripts\python.exe -m pytest -q tests/test_rag_eval_api.py tests/test_rag_eval_service.py tests/test_rag_retention_service.py tests/test_index_outbox_worker.py tests/test_api_routes_contract.py tests/test_rag_diagnostics.py` -> PASS (`17 passed`)
3. `$env:MYSQL_URL=''; $env:DB_PATH='data/test_bot_database.db'; .venv\Scripts\python.exe -m pytest -q tests/test_index_outbox_service.py tests/test_ingestion_outbox.py tests/test_indexing_jobs_lifecycle.py tests/test_qdrant_backend.py` -> PASS (`13 passed`)
4. `python scripts/scan_secrets.py` -> PASS
5. `python scripts/ci_policy_gate.py --working-tree` -> PASS

## Security review

1. No secret material added.
2. Retention delete audit stores bounded structured metadata.
3. Eval endpoints remain API-key protected under existing router dependencies.

## Residual risk

1. Retention logic is time-window based; production cutoffs should be validated against compliance requirements before tightening defaults.
2. Eval metrics currently focus on retrieval relevance heuristics and do not yet enforce statistical CI gates.
