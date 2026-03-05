# Review Report: RAG Orchestrator Phase D Kickoff (Feature-Flag Cutover)

- Date: 2026-03-05
- Reviewer: codex-review
- Verdict: PASS

## Scope reviewed

1. `backend/api/routes/rag.py`
2. `shared/config.py`
3. `env.template`
4. `tests/test_rag_query_definition_intent.py`
5. `SPEC.md`
6. `docs/REQUIREMENTS_TRACEABILITY.md`
7. `docs/design/rag-generalized-architecture-v2.md`
8. `docs/OPERATIONS.md`
9. `docs/CONFIGURATION.md`
10. `docs/USAGE.md`
11. `coordination/tasks.jsonl`
12. `coordination/cycle-contract.json`

## MUST-FIX findings

None.

## SHOULD-FIX findings

1. Add explicit observability marker in retrieval diagnostics for orchestrator mode (`legacy` vs `v4`) to simplify incident triage.
2. Add integration benchmark comparing v4 vs legacy on the same eval suite before switching production default.
3. Continue with parser/model/index epoch governance implementation to close remaining design checklist items.

## Verification

1. `python -m py_compile backend/api/routes/rag.py shared/config.py tests/test_rag_query_definition_intent.py` -> PASS
2. `$env:MYSQL_URL=''; $env:DB_PATH='data/test_bot_database.db'; .venv\Scripts\python.exe -m pytest -q tests/test_rag_query_definition_intent.py` -> PASS (`8 passed`)
3. `$env:MYSQL_URL=''; $env:DB_PATH='data/test_bot_database.db'; .venv\Scripts\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_service.py tests/test_rag_eval_api.py tests/test_api_routes_contract.py` -> PASS (`17 passed`)
4. `python scripts/scan_secrets.py` -> PASS
5. `python scripts/ci_policy_gate.py --working-tree` -> PASS

## Security review

1. No new secrets introduced.
2. New feature flag changes ranking behavior only; API auth boundaries remain unchanged.
3. Rollback path remains single-flag (`RAG_ORCHESTRATOR_V4=false`) with service restart.

## Residual risk

1. `RAG_ORCHESTRATOR_V4` is introduced as opt-in; production behavior is unchanged until explicit cutover.
2. Legacy intent-specific quality remains coupled to hand-tuned boosts while v4 path requires additional benchmark evidence for default switch.
