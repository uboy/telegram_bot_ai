# Design: RAG Markdown/Code Metadata Consistency v1 (P1-2)

Date: 2026-03-06  
Step: `RAGQLTY-006` / `P1-2`

## Goal
- Improve consistency of markdown and code chunk metadata for downstream retrieval/context assembly.

## Changes
- `shared/document_loaders/code_loader.py`:
  - added `doc_title` derivation from filename,
  - added metadata fields for each chunk: `doc_title`, `section_title`, `section_path`, `chunk_no`,
  - improved empty-content fallback metadata to follow same contract.
- `shared/document_loaders/markdown_loader.py`:
  - fixed no-section fallback to populate `section_title`/`section_path` from `doc_title`,
  - normalized section fallback during chunk generation (`doc_title` or `ROOT`),
  - in `chunking_mode=full`, infer `code_lang` if code fences have a single explicit language.

## Tests
- Updated: `tests/test_code_loader.py`.
- Added: `tests/test_markdown_loader_metadata_contract.py`.

## Verification
- `.venv\\Scripts\\python.exe -m pytest -q tests/test_code_loader.py tests/test_markdown_loader_preserves_commands.py tests/test_markdown_loader_metadata_contract.py tests/test_ingestion_metadata_contract.py`
- `python scripts/scan_secrets.py`
- `python scripts/ci_policy_gate.py --working-tree`
