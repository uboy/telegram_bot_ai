# Design: RAG Eval Baseline Runner v1 (P0-3)

Date: 2026-03-06  
Step: `RAGQLTY-003` / `P0-3`

## Goal
- Provide a repeatable baseline-evaluation runner that produces explicit report artifacts.

## Changes
- Added CLI runner: `scripts/rag_eval_baseline_runner.py`.
- Runner executes eval synchronously and writes:
  - timestamped JSON report,
  - timestamped Markdown report,
  - `latest.json` and `latest.md` symlink-style latest snapshots (regular files).
- Added unit tests for:
  - markdown report rendering,
  - slice parsing normalization/dedup.

## Inputs
- `--suite` (default: `rag-general-v1`)
- `--baseline-run-id` (optional)
- `--slices` (optional CSV)
- `--label` (artifact filename prefix)
- `--out-dir` (default: `data/rag_eval_baseline`)

## Outputs
- `data/rag_eval_baseline/<label>_<timestamp>_<run_id>.json`
- `data/rag_eval_baseline/<label>_<timestamp>_<run_id>.md`
- `data/rag_eval_baseline/latest.json`
- `data/rag_eval_baseline/latest.md`

## Why this is needed
- Quality gate alone returns PASS/FAIL but does not persist human-readable baseline evidence by default.
- Baseline runner provides an auditable artifact for review/rollback decisions.

## Verification
- `python -m py_compile scripts/rag_eval_baseline_runner.py tests/test_rag_eval_baseline_runner.py`
- `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_eval_baseline_runner.py tests/test_rag_eval_service.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_dataset_contract.py`
- `python scripts/scan_secrets.py`
- `python scripts/ci_policy_gate.py --working-tree`
