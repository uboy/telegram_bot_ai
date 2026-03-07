# Design: RAG Ingestion Regression Coverage v1 (P1-3)

Date: 2026-03-06  
Step: `RAGQLTY-007` / `P1-3`

## Goal
- Add explicit regression coverage for ingestion metadata/update semantics introduced in P1-1/P1-2.

## Added Coverage
- `tests/test_ingestion_outbox.py`:
  - `test_ingest_web_page_normalizes_chunk_metadata`
  - `test_ingest_codebase_path_sets_code_metadata_contract`
- Existing related coverage (kept in suite):
  - `tests/test_ingestion_metadata_contract.py`
  - `tests/test_ingestion_routes.py`

## What is verified
- Normalized metadata keys are present in real ingestion batch payloads.
- Codebase ingestion keeps code-specific metadata (`code_lang`, `file_path`, `repo_root`) and normalized baseline keys.
- Update/outbox behavior remains stable while metadata contract is enforced.

## Verification
- `.venv\\Scripts\\python.exe -m pytest -q tests/test_ingestion_outbox.py tests/test_ingestion_metadata_contract.py tests/test_ingestion_routes.py`
- `python scripts/scan_secrets.py`
- `python scripts/ci_policy_gate.py --working-tree`
