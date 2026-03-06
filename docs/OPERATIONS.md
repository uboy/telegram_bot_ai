# Operations Runbook

## Local Startup

1. Install deps:
```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

2. Set env:
- Copy `env.template` to `.env`.
- Fill `TELEGRAM_BOT_TOKEN`, `ADMIN_IDS`, DB and backend settings.
- If you use gated/private Hugging Face models, set `HF_TOKEN`.

3. Start backend:
```bash
.venv\Scripts\python -m backend.main
```

4. Start bot:
```bash
.venv\Scripts\python -m frontend.bot
```

## Docker Startup

Use smart launcher:
```bash
python scripts/start_stack.py
```

```powershell
.\scripts\start_stack.ps1
```

```bash
./scripts/start_stack.sh
```

- `MYSQL_URL` set -> launcher enables `mysql` compose profile and starts `db`.
- `MYSQL_URL` missing/empty -> launcher starts stack without `db`.
- Dry-run mode:
```bash
python scripts/start_stack.py --dry-run
```

```powershell
.\scripts\start_stack.ps1 --dry-run
```

## Health Checks

- API health: `GET /api/v1/health`
- Ingestion jobs: `GET /api/v1/jobs/{job_id}`
- ASR jobs: `GET /api/v1/asr/jobs/{job_id}`
- Analytics digest status: `GET /api/v1/analytics/digests/{digest_id}`
- RAG diagnostics: `GET /api/v1/rag/diagnostics/{request_id}`

## RAG Backend Mode

- Production default: `RAG_BACKEND=qdrant` (external dense retrieval + local lexical channel).
- Rollback mode: `RAG_BACKEND=legacy` (in-process FAISS/BM25 path).
- Qdrant connection knobs:
  - `QDRANT_URL`
  - `QDRANT_API_KEY` (optional)
  - `QDRANT_COLLECTION`
  - `QDRANT_TIMEOUT_SEC`

After changing any RAG backend env vars, restart `backend` and `bot` services.

## RAG Orchestrator Mode

- Legacy mode: `RAG_ORCHESTRATOR_V4=false`
  - keeps route-level intent boosts and keyword fallback logic.
- Phase D mode: `RAG_ORCHESTRATOR_V4=true`
  - switches `/api/v1/rag/query` to primary ranking without query-specific hardcoded boosts/fallback.
- Rollback: set `RAG_ORCHESTRATOR_V4=false` and restart `backend` + `bot`.

Diagnostics note:
- `GET /api/v1/rag/diagnostics/{request_id}` now exposes `orchestrator_mode` (`legacy`/`v4`) for request-level triage.

## Index Outbox Worker

When `RAG_BACKEND=qdrant`, backend starts async index-sync worker:
- consumes `index_outbox_events`,
- retries transient failures with backoff,
- moves exhausted events to dead-letter status,
- periodically writes SQL-vs-Qdrant drift snapshots into `index_sync_audit`.

Main knobs:
- `RAG_INDEX_OUTBOX_WORKER_ENABLED`
- `RAG_INDEX_OUTBOX_POLL_INTERVAL_SEC`
- `RAG_INDEX_OUTBOX_BATCH_SIZE`
- `RAG_INDEX_OUTBOX_MAX_ATTEMPTS`
- `RAG_INDEX_OUTBOX_RETRY_BASE_SEC`
- `RAG_INDEX_OUTBOX_RETRY_MAX_SEC`
- `RAG_INDEX_DRIFT_AUDIT_INTERVAL_SEC`
- `RAG_INDEX_DRIFT_MAX_KBS`
- `RAG_INDEX_DRIFT_WARNING_RATIO`
- `RAG_INDEX_DRIFT_CRITICAL_RATIO`

## Retention Lifecycle

Retention cleanup runs inside the background worker loop:
- purges old retrieval diagnostics logs,
- purges old document versions/chunks,
- purges stale eval artifacts and drift audit snapshots,
- records every purge in `retention_deletion_audit`.

Main knobs:
- `RAG_RETENTION_ENABLED`
- `RAG_RETENTION_INTERVAL_SEC`
- `RAG_RETENTION_QUERY_LOG_DAYS`
- `RAG_RETENTION_DOC_OLD_VERSION_DAYS`
- `RAG_RETENTION_EVAL_DAYS`
- `RAG_RETENTION_DRIFT_AUDIT_DAYS`
- `RAG_RETENTION_AUDIT_DAYS`

## RAG Eval Operations

- Launch eval run: `POST /api/v1/rag/eval/run`
- Check run status/results: `GET /api/v1/rag/eval/{run_id}`
- Default suite file: `tests/data/rag_eval_ready_data_v1.yaml` (override with `RAG_EVAL_SUITE_FILE`)
- Threshold knobs:
  - `RAG_EVAL_THRESHOLD_RECALL_AT10`
  - `RAG_EVAL_THRESHOLD_MRR_AT10`
  - `RAG_EVAL_THRESHOLD_NDCG_AT10`
- Apply quality gate for completed run:
  - `python scripts/rag_eval_quality_gate.py --run-id <run_id> --baseline-run-id <baseline_run_id> --print-json`
- Apply quality gate from report artifacts (no DB run lookup):
  - `python scripts/rag_eval_quality_gate.py --run-report-json data/rag_eval_baseline/latest.json --allow-no-baseline --print-json`
  - `python scripts/rag_eval_quality_gate.py --run-report-json <run_report.json> --baseline-report-json <baseline_report.json> --print-json`
- Produce baseline artifacts (JSON + Markdown):
  - `python scripts/rag_eval_baseline_runner.py --suite rag-general-v1 --label baseline_v1 --out-dir data/rag_eval_baseline`
  - outputs include timestamped reports and `data/rag_eval_baseline/latest.{json,md}` snapshots.

## Legacy vs v4 Compare Run

Use comparator script against two running backend instances:
- `python scripts/rag_orchestrator_compare.py --legacy-base-url <legacy_url> --v4-base-url <v4_url> --api-key <API_KEY> --kb-id <KB_ID> --cases-file tests/data/rag_eval_ready_data_v1.yaml --json-out data/rag_compare_report.json`
- Optional gate:
  - `--max-source-hit-drop 0.10` fails run if `v4` loses more than 10pp `source_hit_rate` vs legacy.

For the default docker-compose stack, use wrapper script:
- `bash scripts/run_rag_compare_stack.sh --max-source-hit-drop 0.10`
- report path (host): `./data/rag_compare_report.json`
- wrapper auto-starts `backend redis qdrant` if legacy backend container is absent
- auto-prepare test KB from `test.pdf` in repo root:
  - `bash scripts/run_rag_compare_stack.sh --prepare-test-kb --test-pdf test.pdf --max-source-hit-drop 0.10`
- when `--prepare-test-kb` is used, wrapper auto-generates eval cases from uploaded PDF chunks by default
  - disable with `--no-auto-cases` or override with explicit `--cases-file <path>`
- by default temporary `v4` is started with `RAG_DEVICE=cpu`; override if needed:
  - `bash scripts/run_rag_compare_stack.sh --v4-rag-device cuda --max-source-hit-drop 0.10`
- comparator network retry tuning (useful on first cold model load):
  - `bash scripts/run_rag_compare_stack.sh --prepare-test-kb --test-pdf test.pdf --connect-retries 180 --retry-sleep-sec 1.5 --max-source-hit-drop 0.10`
- selected-context quality gate:
  - wrapper passes `--min-selected-rate 0.01` to comparator by default and fails run if retrieval context is effectively empty.

## Container Runtime Notes

- Redis: on some Docker runtimes container-level `sysctls` are not allowed.  
  Set `vm.overcommit_memory=1` on the host if you need to suppress Redis overcommit warnings.
- n8n: compose config enforces secure settings file permissions (`N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=true`).
- MySQL: compose starts `mysqld` with non-deprecated auth/cache options (`--authentication-policy`, `--host-cache-size=0`).

## Security Checks

- Confirm `BACKEND_API_KEY` is set in production.
- Verify protected routes reject requests without `X-API-Key`.
- Keep `.env` untracked in git.

## Incident Quick Actions

- ASR queue overload:
  - reduce incoming load
  - inspect `ASR_QUEUE_MAX`, `ASR_MAX_WORKERS`
  - restart backend workers if stalled

- Ingestion failures:
  - poll `/jobs/{job_id}` and inspect `error`
  - validate file/path/url input and external connectivity

- RAG quality regression:
  - capture `request_id` from `/api/v1/rag/query` response
  - inspect `/api/v1/rag/diagnostics/{request_id}` for candidate trace
  - if Qdrant errors spike, switch `RAG_BACKEND=legacy` and restart services
  - check `degraded_mode`/`degraded_reason` in diagnostics for dense-channel degradation markers

- Outbox backlog/drift incident:
  - inspect outbox statuses in `index_outbox_events` (`pending`, `processing`, `dead`)
  - inspect latest drift rows in `index_sync_audit` for `warning`/`critical` status
  - if backlog grows with repeated Qdrant errors, temporarily switch `RAG_BACKEND=legacy`
  - after backend recovery, replay pending events (worker retries automatically)

- Retention incident:
  - inspect recent rows in `retention_deletion_audit` (`status`, `rows_deleted`, `details_json`)
  - verify retention env values and worker uptime
  - if aggressive cleanup detected, disable via `RAG_RETENTION_ENABLED=false` and restart backend, then investigate

- Eval run incident:
  - inspect `GET /api/v1/rag/eval/{run_id}` status and `error_message`
  - validate suite file path (`RAG_EVAL_SUITE_FILE`) and YAML readability
  - rerun with explicit slices for narrower diagnosis

- Analytics failures:
  - inspect digest/import status endpoints
  - check scheduler and DB availability

## Rollback

- Revert to previous image/tag.
- Disable affected menu path in bot callbacks if needed.
- Keep DB schema backward-compatible for v1 rollback.
- For retrieval stack rollback, set `RAG_BACKEND=legacy` and restart backend/bot.
