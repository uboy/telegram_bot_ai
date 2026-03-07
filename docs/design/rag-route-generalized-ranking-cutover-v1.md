# Design: RAG Route Generalized Ranking Cutover v1 (P2-1)

Date: 2026-03-06  
Step: `RAGQLTY-008` / `P2-1`

## Goal
- Remove brittle route-level query-specific boosts/fallback from default runtime behavior.

## Changes
- Added config switch `RAG_LEGACY_QUERY_HEURISTICS`:
  - default: `false` (generalized route behavior),
  - `true`: temporary rollback path to legacy query-intent boosts/fallback.
- Updated `/rag/query` path in `backend/api/routes/rag.py`:
  - if legacy heuristics disabled -> use generalized ranking path (`base_score`) and skip route-level keyword fallback,
  - if enabled -> preserve previous legacy heuristics behavior.
- Kept `RAG_ORCHESTRATOR_V4` behavior unchanged; v4 still bypasses legacy heuristics.

## Why this is generalized
- Default route logic no longer relies on intent-specific hardcoded boosts/fallback.
- Ranking defaults to retrieval/reranker signals + generic base ordering.

## Rollback
- Set `RAG_LEGACY_QUERY_HEURISTICS=true` to restore legacy query-intent boosts/fallback behavior.

## Verification
- `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_rag_diagnostics.py`
- `python scripts/scan_secrets.py`
- `python scripts/ci_policy_gate.py --working-tree`
