# Design: RAG Eval Ready Corpus v1 (P0-2)

Date: 2026-03-06  
Step: `RAGQLTY-002` / `P0-2`

## Goal
- Introduce a fixed, versioned eval corpus for repeatable RAG regression checks.

## Changes
- Added fixed corpus file: `tests/data/rag_eval_ready_data_v1.yaml`.
- Switched default eval suite path to this corpus in `backend/services/rag_eval_service.py`.
- Added corpus contract test: `tests/test_rag_eval_dataset_contract.py`.

## Corpus Contract
- At least 24 test cases.
- Each case must contain:
  - `id` (unique),
  - `query`,
  - `expected_source`,
  - `expected_snippets` (non-empty list).
- Corpus must cover required slices:
  - `ru`, `en`, `mixed`, `factoid`, `howto`, `legal`, `numeric`, `long-context`.

## Why this is generalized
- Dataset shape is domain-agnostic and does not encode retrieval heuristics.
- No query-specific code paths are added.

## Risks
- Corpus quality can drift if source docs evolve.
- Mitigation: versioned corpus file + explicit contract test + future baseline refresh cycle.

## Verification
- `python -m py_compile backend/services/rag_eval_service.py tests/test_rag_eval_dataset_contract.py`
- `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_eval_service.py tests/test_rag_eval_dataset_contract.py tests/test_rag_eval_quality_gate.py`
- `python scripts/scan_secrets.py`
- `python scripts/ci_policy_gate.py --working-tree`
