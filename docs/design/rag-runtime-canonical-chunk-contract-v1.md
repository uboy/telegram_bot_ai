# Design: RAG Runtime Canonical Chunk Contract v1

Date: 2026-03-09
Owner: codex (architect phase for `RAGEXEC-013`)
Status: APPROVED:v1 / IMPLEMENTED IN RAGEXEC-013

Related docs:
- `docs/design/rag-near-ideal-task-breakdown-v1.md`
- `docs/design/rag-generalized-architecture-v2.md`
- `docs/design/rag-search-improvement-program-v1.md`
- `docs/design/rag-ingestion-metadata-contract-v1.md`

## 1. Summary

### Problem statement
Current runtime ingestion normalizes only a minimal metadata contract around `type`, `title`, `doc_title`, `section_title`, `section_path`, `chunk_kind`, `document_class`, `language`, `doc_hash`, `doc_version`, and `source_updated_at`. That is enough for compatibility, but not enough for near-ideal retrieval/context assembly:
- chunk adjacency is not explicit,
- parser provenance is not explicit,
- structural location fields are inconsistent or absent,
- SQL cannot query or audit richer chunk structure without re-parsing JSON metadata.

### Goals
- Define one additive canonical chunk contract for runtime ingestion.
- Start persisting canonical chunk structure in both metadata JSON and first-class SQL columns.
- Keep rollout backward-compatible with existing chunks, loaders, retrieval, and outbox behavior.
- Make the contract precise enough that later slices (`RAGEXEC-014..017`) can build on it without inventing new field semantics.

### Non-goals
- No parser-fidelity rewrite in this slice.
- No retrieval ranking/context assembly logic changes in this slice.
- No destructive migration or hard backfill requirement before rollout.
- No dependency changes.

## 2. Scope boundaries

### In scope
- `backend/services/ingestion_service.py`
- `shared/database.py`
- additive canonical fields for `knowledge_chunks`
- canonical metadata builder semantics
- migration plan for additive nullable columns
- regression expectations for ingestion metadata/outbox coverage

### Out of scope
- PDF/DOCX parser improvements (`RAGEXEC-014`)
- web/wiki/code normalization improvements beyond canonical field plumbing (`RAGEXEC-015`)
- evidence-pack retrieval/context changes (`RAGEXEC-016..017`)
- judge/eval threshold work (`RAGEXEC-018`)

## 3. Assumptions and constraints

- The project uses startup-time additive SQL migrations in `shared/database.py`, not Alembic.
- Existing chunks must remain readable without backfill.
- Existing APIs and bot flows must stay backward-compatible.
- New fields should be nullable/additive and safe for both SQLite and MySQL.
- Real local corpora remain local-only; no private data may appear in committed tests/docs.
- No new external packages are allowed in this slice.

## 4. Architecture

### Components
- `document_loader_manager` / loaders:
  - continue producing best-effort chunk metadata.
- `IngestionService`:
  - becomes the canonical metadata normalizer and dual-writer.
- `KnowledgeChunk` SQL row:
  - stores canonical scalar fields as queryable columns plus full metadata JSON.
- downstream retrieval:
  - remains backward-compatible and continues reading metadata JSON unless later slices opt into the new columns.

### Data flow
1. Loader returns chunk content + best-effort metadata.
2. `IngestionService` assigns canonical defaults and normalizes optional structural fields.
3. Canonical fields are written:
   - into `metadata_json` / `chunk_metadata`,
   - into additive `knowledge_chunks` columns where available.
4. Existing outbox/index flows continue unchanged; later slices may consume the richer fields.

## 5. Interfaces and contracts

### 5.1 Canonical chunk metadata contract

Runtime canonical metadata for every chunk must expose:

Required on every new chunk:
- `type`
- `title`
- `doc_title`
- `section_title`
- `section_path`
- `section_path_norm`
- `chunk_kind`
- `block_type`
- `document_class`
- `language`
- `doc_version`
- `source_updated_at`
- `chunk_no`
- `chunk_hash`
- `token_count_est`
- `parser_profile`

Optional / nullable:
- `doc_hash`
- `page_no`
- `char_start`
- `char_end`
- `parser_confidence`
- `parser_warning`
- `parent_chunk_id`
- `prev_chunk_id`
- `next_chunk_id`

### 5.2 Internal normalization rules

`IngestionService._normalize_chunk_metadata(...)` should evolve so that:
- `chunk_no` is assigned deterministically from chunk order within one source version.
- `chunk_hash` is stable for the same chunk content + source identity + `chunk_no`.
- `block_type` defaults to `chunk_kind`, otherwise `text`.
- `section_path_norm` is a normalized search/audit form of `section_path`.
- `token_count_est` is always populated using a lightweight deterministic estimate.
- `parser_profile` is always populated, at minimum `loader:<source_type>:v1`.
- `parser_confidence`, `parser_warning`, adjacency ids stay nullable unless provided by a richer loader or later backfill.

### 5.3 SQL contract for `knowledge_chunks`

Additive nullable columns:
- `chunk_hash`
- `chunk_no`
- `block_type`
- `parent_chunk_id`
- `prev_chunk_id`
- `next_chunk_id`
- `section_path_norm`
- `page_no`
- `char_start`
- `char_end`
- `token_count_est`
- `parser_profile`
- `parser_confidence`
- `parser_warning`

Initial runtime rule:
- JSON metadata remains the compatibility source of truth.
- New columns mirror canonical values for new writes.
- Existing rows may keep NULL in the new columns until explicit backfill.

### 5.4 Error handling strategy

- Missing optional structural fields: fill defaults / NULLs, do not fail ingest.
- Invalid numeric parser fields from loaders: coerce safely or drop to NULL with warning.
- Hash/normalization failure: fail closed to deterministic fallback values, not to missing contract.
- Migration failure for additive columns: log clearly and block startup only if base tables become unusable.

## 6. Data model changes and migrations

### 6.1 `knowledge_chunks`

Add the columns listed in section 5.3 as nullable/additive.

### 6.2 Migration strategy

Use the existing startup migration pattern in `shared/database.py`:
- inspect current schema,
- add missing columns with `ALTER TABLE`,
- do not rewrite or delete existing data.

### 6.3 Backfill policy

For `RAGEXEC-013`:
- no mandatory full-table backfill,
- new writes must populate both JSON + scalar columns,
- optional future maintenance script may backfill active chunks once the contract stabilizes.

## 7. Edge cases and failure modes

- Older rows missing new columns or JSON keys.
- Loaders that emit incomplete metadata.
- Mixed-language documents with weak language detection.
- Extremely small or empty chunks producing unstable hashes.
- Parser-specific location fields that only some source types can fill.
- MySQL vs SQLite differences in additive-column migration behavior.

Mitigations:
- keep defaults deterministic,
- keep nullable fields nullable,
- preserve existing JSON contract,
- make tests assert presence/shape, not impossible parser precision.

## 8. Security requirements

- No auth changes in this slice.
- No secrets or local corpus paths may enter canonical metadata.
- `parser_warning` must never include secrets, credentials, auth headers, bearer tokens, password/token/api-key values, or full private URLs with credentials.
- No new dependencies.
- Secret scan remains mandatory before completion.

## 9. Performance requirements

- Metadata normalization must remain linear in chunk count.
- `chunk_hash` and `token_count_est` must be lightweight.
- Additive columns must not require per-row extra DB lookups.
- Default ingest throughput should not materially regress for common document sizes.

## 10. Observability

Add/retain logs for:
- migration add-column events,
- canonical metadata normalization warnings,
- loader-provided parser metadata that is dropped or coerced.

Future metrics/alerts:
- ratio of new chunks missing optional parser structure by source type,
- migration failures per deployment,
- canonical field population drift across loaders.

## 11. Test plan

Unit/integration coverage:
- extend `tests/test_ingestion_metadata_contract.py` with canonical field assertions,
- extend `tests/test_ingestion_outbox.py` to ensure richer metadata does not break outbox payload behavior,
- add focused `migrate_database()` coverage for additive `knowledge_chunks` columns on a legacy table shape,
- cover direct wiki HTML/git/zip `rag_system.add_chunk(...)` paths so required canonical fields are still dual-written.

Exact commands:
- `python -m py_compile backend/services/ingestion_service.py shared/rag_system.py shared/wiki_scraper.py shared/wiki_git_loader.py shared/database.py tests/test_ingestion_metadata_contract.py tests/test_ingestion_outbox.py tests/test_wiki_scraper.py`
- `.venv\Scripts\python.exe -m pytest -q tests/test_ingestion_metadata_contract.py tests/test_ingestion_outbox.py tests/test_wiki_scraper.py`
- `python scripts/scan_secrets.py`
- `python scripts/ci_policy_gate.py --working-tree`

## 12. Rollout plan

1. Additive schema columns in `shared/database.py`.
2. Dual-write canonical metadata for new ingests in `IngestionService`.
3. Expand regression coverage.
4. Observe real ingest writes for null/default patterns.
5. Only after that let later slices consume the new fields for parser fidelity or retrieval.

## 13. Rollback plan

- Roll back consumers first; they must tolerate NULL/new JSON keys.
- Leave additive columns in place if needed; rollback does not require destructive schema reversal.
- If a write-path bug appears, disable or remove dual-write population while keeping columns unused.

## 14. Acceptance criteria checklist

- New ingests always write canonical `chunk_no`, `chunk_hash`, `section_path_norm`, `block_type`, `token_count_est`, and `parser_profile`.
- Optional parser/adjacency fields are present as nullable contract keys/columns, not undefined ad-hoc values.
- `knowledge_chunks` additive columns exist after startup migration on supported DBs.
- Existing ingest flows remain backward-compatible.
- Metadata/outbox regressions stay green.

## 15. Spec/doc update plan

Implementation must update:
- `SPEC.md`
- `docs/REQUIREMENTS_TRACEABILITY.md`
- `docs/OPERATIONS.md`
- `docs/design/rag-near-ideal-task-breakdown-v1.md`

Optional if implementation changes external API/debug output:
- `docs/API_REFERENCE.md`
- `docs/USAGE.md`

## 16. Secret-safety impact

Potential leak surfaces:
- migration/debug logs,
- parser warnings,
- canonical metadata copied from loader metadata.

Protections:
- never log raw credentials/headers,
- do not copy private URL credentials into normalized fields,
- keep committed tests synthetic/public-safe,
- keep local corpora completely out of the repo and docs.

## Approval

REVIEW REQUIRED — Reply "APPROVED:v1" or "CHANGES:<bullets>"
