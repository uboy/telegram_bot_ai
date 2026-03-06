# Design: RAG Ingestion Metadata Contract v1 (P1-1)

Date: 2026-03-06  
Step: `RAGQLTY-005` / `P1-1`

## Goal
- Normalize chunk metadata shape across ingestion paths without rewriting each loader.

## Approach
- Introduce centralized metadata normalizer in `IngestionService`:
  - `_normalize_chunk_metadata(...)`.
- Apply normalizer in all main ingestion branches:
  - web page ingestion,
  - archive inner-file ingestion,
  - chat ingestion,
  - single-document ingestion,
  - codebase ingestion,
  - image ingestion metadata payload.

## Contract fields
Normalized metadata now guarantees these baseline keys:
- `type`
- `title`
- `doc_title`
- `section_title`
- `section_path`
- `chunk_kind`
- `document_class`
- `language`
- `doc_version`
- `source_updated_at`

Optional key:
- `doc_hash` (when available for source type/path).

## Compatibility
- Existing loader-specific fields are preserved.
- Existing explicit fields from loaders take precedence over defaults.
- No retrieval ranking logic changes in this step.

## Verification
- `python -m py_compile backend/services/ingestion_service.py tests/test_ingestion_metadata_contract.py`
- `.venv\\Scripts\\python.exe -m pytest -q tests/test_ingestion_metadata_contract.py tests/test_ingestion_routes.py tests/test_ingestion_outbox.py`
- `python scripts/scan_secrets.py`
- `python scripts/ci_policy_gate.py --working-tree`
