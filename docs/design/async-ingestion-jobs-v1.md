# Feature Design Spec: Async Ingestion Jobs API (v1)

## 1) Summary
**Problem statement**: Heavy ingestion operations (documents, web pages, code, images) can run long and need observable progress without blocking clients.

**Goals**
- Run ingestion requests asynchronously.
- Return `job_id` immediately from ingestion endpoints.
- Provide unified job polling endpoint.

**Non-goals**
- Distributed task queue (Celery/RQ).
- Cross-process retries and dead-letter queues.

## 2) Scope boundaries
**In-scope**
- `POST /api/v1/ingestion/web|document|image|code-path|code-git` return `job_id`.
- `GET /api/v1/jobs/{job_id}` returns status/progress/stage/error.
- Background execution via in-process threads.

**Out-of-scope**
- Async conversion for `wiki-crawl|wiki-git|wiki-zip` in v1.
- SLA guarantees for horizontal scaling.

## 3) Assumptions + constraints
- Follow AGENTS.md workflow and minimal diffs.
- No new dependencies.
- Existing DB model `Job` is available and persisted in SQL DB.

## 4) Architecture
**Components**
- `backend/api/routes/ingestion.py` creates jobs and schedules work.
- `backend/services/indexing_service.py` executes jobs and updates statuses.
- `backend/api/routes/jobs.py` exposes job status polling.

**Data flow**
1. Client calls ingestion endpoint.
2. Backend creates `Job(status=pending)` and returns `job_id`.
3. Background thread runs ingestion service.
4. Job transitions to `processing` then `completed` or `failed`.
5. Client polls `/jobs/{job_id}`.

## 5) Interfaces / contracts
**Public APIs**
- `POST /api/v1/ingestion/*` -> `job_id` in response payload.
- `GET /api/v1/jobs/{job_id}` -> `{job_id, status, progress, stage, error}`.

**Internal boundaries**
- `IndexingService.create_job(stage: str) -> Job`
- `IndexingService.run_async(target, job_id, payload) -> None`
- `IndexingService.update_job(...) -> None`

**Error handling**
- Any ingestion exception marks job `failed` with `error_message`.
- Temporary files are removed in `finally` blocks where applicable.

## 6) Data model changes + migrations
- Uses existing `Job` model; no new migration required for v1.

## 7) Edge cases + failure modes
- Job id not found -> `404`.
- Worker exception -> `failed` with error text.
- Temp file cleanup failure -> logged, does not crash request path.

## 8) Security requirements
- Ingestion and jobs endpoints require `X-API-Key`.
- Validate inputs through existing FastAPI schemas and route validation.
- Do not log secrets in job payloads.

## 9) Performance requirements
- Ingestion request should respond quickly with `job_id`.
- Job update operations must be O(1) per update and DB write bounded.

## 10) Observability
- Log job creation/start/completion/failure with `job_id` and stage.
- Polling endpoint serves current persisted state.

## 11) Test plan
- Unit: `IndexingService` success/failure lifecycle.
- API: ingestion endpoints return `job_id`, jobs endpoint returns status.
- Security: `jobs` and `ingestion` endpoints guarded by API key dependency.

**Commands**
- `python -m pytest`

## 12) Rollout plan + rollback plan
- Rollout: deploy backend with jobs routes and indexing service.
- Rollback: disable async path and return synchronous ingestion path if needed.

## 13) Acceptance criteria checklist
- Ingestion endpoints return `job_id` immediately.
- Job transitions to `processing` then `completed` or `failed`.
- `/jobs/{job_id}` exposes stage/progress/error consistently.
- Protected endpoints require API key.

---

Approval

APPROVED:v1
