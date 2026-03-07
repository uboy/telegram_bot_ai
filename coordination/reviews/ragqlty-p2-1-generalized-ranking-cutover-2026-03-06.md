# Review Report: RAGQLTY-008 (P2-1)

- Date: 2026-03-06
- Scope: remove default route-level query-specific boosts/fallback from ranking path
- Verdict: PASS

## Reviewed artifacts
- `backend/api/routes/rag.py`
- `shared/config.py`
- `env.template`
- `tests/test_rag_query_definition_intent.py`
- `docs/design/rag-route-generalized-ranking-cutover-v1.md`
- `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/OPERATIONS.md`

## Findings
- Added explicit `RAG_LEGACY_QUERY_HEURISTICS` rollback switch.
- Default route behavior now avoids query-specific boosts/fallback in legacy mode.
- v4 behavior unchanged and remains generalized.
- Tests updated to preserve legacy-path coverage behind explicit heuristic flag and verify new generalized default.

## Verification
- `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_rag_diagnostics.py` -> PASS (`13 passed`)
- `python scripts/scan_secrets.py` -> PASS
- `python scripts/ci_policy_gate.py --working-tree` -> PASS
