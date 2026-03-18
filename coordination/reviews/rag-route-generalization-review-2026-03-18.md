# Review Report: RAG Route-Level HOWTO Generalization

Date: 2026-03-18
Reviewer: codex-review (independent pass)
Reviewed scope: `backend/api/routes/rag.py`, `tests/test_rag_compound_howto_focus.py`, `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/design/rag-service-architecture-and-pipeline-v1.md`, `coordination/state/codex.md`

## Findings

- MUST-FIX issues

  - None.

- SHOULD-FIX issues

  - `backend/api/routes/rag.py:1198-1255`, `backend/api/routes/rag.py:1263-1269`, and `backend/api/routes/rag.py:1869-1910` still encode a fixed English/Russian procedural vocabulary and fallback action list. That is materially better than the previous literal `Sync&Build` bias, but it remains language-tuned and will likely underperform for other corpora or query languages that do not share those markers.

- Spec mismatches

  - None.

## Verification

- `python -m py_compile backend/api/routes/rag.py tests/test_rag_compound_howto_focus.py`
  - PASS
- `$env:MYSQL_URL=''; $env:DB_PATH='data/reviewer-rag.db'; .venv\Scripts\python.exe -m pytest -q tests\test_rag_compound_howto_focus.py`
  - PASS (`4 passed, 4 warnings`)
- `python scripts/scan_secrets.py`
  - PASS
- `rg --files | Select-String "validate-review-report"`
  - No matches found in this checkout, so I could not run a repository validator script from the expected path.

Verification summary:

- The new query-derived procedural focus helper behaves as intended for the regression cases in `tests/test_rag_compound_howto_focus.py`.
- The route-level HOWTO scoring no longer depends on the literal `Sync&Build` page name.
- Secret scanning stayed clean.

## Final Verdict

- PASS

## Clarifications

- None.
