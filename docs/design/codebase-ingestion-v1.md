# Feature Design Spec: Codebase Ingestion (Path + Git) (v1)

## 1) Summary
**Problem statement**: Users need to ingest large source-code trees into KB for code-aware RAG responses.

**Goals**
- Support local path ingestion and Git URL ingestion.
- Return async job id and process in background.
- Persist file/chunk metadata for retrieval quality.

**Non-goals**
- Full AST semantic indexing.
- Language-server integration.

## 2) Scope boundaries
**In-scope**
- `POST /api/v1/ingestion/code-path`
- `POST /api/v1/ingestion/code-git`
- Background indexing via `IndexingService`.

**Out-of-scope**
- Arbitrary remote credential management for private repositories.
- Runtime sandboxing beyond current process constraints.

## 3) Assumptions + constraints
- No new dependencies.
- Existing code loader and ingestion service are used.
- API key required.

## 4) Architecture
**Components**
- `backend/api/routes/ingestion.py` code endpoints.
- `backend/services/indexing_service.py` async job orchestration.
- `backend/services/ingestion_service.py` code ingestion logic.

**Data flow**
1. Client requests code ingestion (path or git URL).
2. Backend creates job and returns `job_id`.
3. Worker calls ingestion service in background.
4. Job status available through `/api/v1/jobs/{job_id}`.

## 5) Interfaces / contracts
**Public APIs**
- `/ingestion/code-path` request: `{knowledge_base_id, path, repo_label?, telegram_id?, username?}`
- `/ingestion/code-git` request: `{knowledge_base_id, git_url, telegram_id?, username?}`
- response: `{kb_id, root, files_processed, files_skipped, files_updated, chunks_added, job_id}`

**Internal boundaries**
- `IndexingService.run_code_job(job_id, payload, mode)`
- `IngestionService.ingest_codebase_path(...)`
- `IngestionService.ingest_codebase_git(...)`

**Error handling**
- Failures set job status to `failed` with error details.
- Request validation handled by Pydantic schemas.

## 6) Data model changes + migrations
- No new DB table required in v1.

## 7) Edge cases + failure modes
- Invalid local path.
- Unreachable or invalid Git URL.
- Large repositories and timeouts.
- Unsupported/binary files should be skipped safely.

## 8) Security requirements
- API key required.
- Validate and constrain paths in deployment environment.
- Avoid logging secrets from git URLs.

## 9) Performance requirements
- Incremental updates by file hash where supported.
- Bounded memory usage for large repository traversal.

## 10) Observability
- Log counts for processed/skipped/updated files and chunk totals.
- Log job lifecycle with stage transitions.

## 11) Test plan
- Route contract tests for both endpoints.
- Job lifecycle tests for success/failure.
- Security tests for API key protection.

**Commands**
- `python -m pytest`

## 12) Rollout plan + rollback plan
- Rollout: enable code ingestion buttons and endpoints.
- Rollback: disable code ingestion callbacks and endpoints.

## 13) Acceptance criteria checklist
- Both path and git ingestion endpoints exist and return job ids.
- Jobs transition through expected statuses.
- Responses include code ingestion counters.
- Endpoints protected by API key.

---

Approval

APPROVED:v1
