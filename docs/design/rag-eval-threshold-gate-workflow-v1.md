# Design: RAG Eval Threshold Gate Workflow v1 (P0-4)

Date: 2026-03-06  
Step: `RAGQLTY-004` / `P0-4`

## Goal
- Integrate threshold-based quality gate into practical test workflow with explicit PASS/FAIL semantics.

## Changes
- Extended `scripts/rag_eval_quality_gate.py` with artifact mode:
  - `--run-report-json <path>`
  - `--baseline-report-json <path>`
- Added parser/helpers to evaluate gate directly from baseline runner JSON artifacts (without DB fetch dependency).
- Kept existing DB `--run-id` mode backward compatible.
- Updated CI workflow to run eval-gate related tests and compile baseline runner.

## Why this matters
- Teams can run quality-gate in two modes:
  - DB run mode for server-side eval pipeline,
  - artifact mode for local/CI/report-based comparisons.
- This makes gate integration deterministic and easier to automate.

## Verification
- `python -m py_compile scripts/rag_eval_quality_gate.py scripts/rag_eval_baseline_runner.py tests/test_rag_eval_quality_gate.py`
- `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py tests/test_rag_eval_dataset_contract.py tests/test_rag_eval_service.py`
- `python scripts/scan_secrets.py`
- `python scripts/ci_policy_gate.py --working-tree`
