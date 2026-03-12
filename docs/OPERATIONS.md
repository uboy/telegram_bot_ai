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
- Candidate rows in diagnostics now also expose final-context inclusion markers (`included_in_context`, `context_rank`, `context_reason`, `context_anchor_rank`); evidence-pack support rows that were not part of the raw retrieval top-N appear with `origin/channel=context_support`.
- Retrieval diagnostics persistence now flushes the parent `retrieval_query_logs` row before candidate inserts; this avoids transient MySQL FK-ordering warnings (`retrieval_candidate_logs_ibfk_1`) during live eval and production triage.

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
- Default suite file: `tests/data/rag_eval_ready_data_v2.yaml` (override with `RAG_EVAL_SUITE_FILE`)
- Source-manifest contract: `tests/data/rag_eval_source_manifest_v1.yaml`
- Real verification corpora must stay local-only; committed eval artifacts must not contain raw chat export or developer-local absolute paths.
- Threshold knobs:
  - `RAG_EVAL_THRESHOLD_RECALL_AT10`
  - `RAG_EVAL_THRESHOLD_MRR_AT10`
  - `RAG_EVAL_THRESHOLD_NDCG_AT10`
- Slice-aware thresholds:
  - eval runs now persist `metrics.slice_thresholds` so source-family and failure-mode slices can use stricter/looser defaults than the aggregate gate,
  - quality gate prefers persisted per-slice thresholds from the run artifact over the flat CLI defaults when both are present.
- Local-only answer/judge lane:
  - enable with `RAG_EVAL_ENABLE_ANSWER_METRICS=true`; add `RAG_EVAL_ENABLE_JUDGE_METRICS=true` to require Ollama judge scoring,
  - answer path reuses the main provider/model already configured in `.env`; judge path may optionally override provider/model through `RAG_EVAL_JUDGE_PROVIDER` and `RAG_EVAL_JUDGE_MODEL`,
  - `RAG_EVAL_OLLAMA_BASE_URL` matters only when the active answer or judge provider is `ollama`,
  - `RAG_EVAL_KB_ID` can pin the run to one dedicated KB so local eval does not mix with unrelated knowledge bases,
  - auto local runs derive `metrics.slices` from the actual suite coverage so filtered reports do not emit irrelevant zero-sample groups,
  - explicit `--slices` overrides remain strict and are still allowed to fail on uncovered slices when debugging gate behavior,
  - artifacts record `answer_provider`, `judge_provider`, sanitized `effective_ollama_base_url` when Ollama is active, `answer_model`, `judge_model`, `git_sha`, `git_dirty`, `screening_summary`, `security_summary`, `case_failures`, and `suspicious_events`,
  - local answer-eval artifacts also record `case_analysis` for failed/suspicious cases with compact query/answer previews, reasons, suspicious events, metric snapshots, and source hints so answer regressions can be triaged without rerunning every case interactively,
  - committed-safe CI must keep these env vars unset.
- Apply quality gate for completed run:
  - `python scripts/rag_eval_quality_gate.py --run-id <run_id> --baseline-run-id <baseline_run_id> --print-json`
  - required slices default to `overall` + core topical slices and expand automatically for recorded `metrics.slices` plus source-family/security slices present in run metadata unless `--slices` is provided explicitly
  - if `--metrics` is omitted, the gate uses `metrics.available_metrics` from the run artifact when present; otherwise it falls back to retrieval defaults
  - gate output now separates `source_families`, `security_scenarios`, and `failure_modes` instead of flattening all non-topical slices into one bucket
  - zero-sample recorded/core slices stay in the gate and fail explicitly through `missing_metric_row` / insufficient-sample checks instead of being silently skipped
  - CI `agent-quality-gates` uses a fail-fast order: policy gate -> secret scan -> eval tooling compile -> synthetic eval contract tests -> smoke tests
  - the committed CI lane must remain public-safe and must not depend on Ollama or developer-local corpora
  - retrieval-only reports remain free of answer-lane provider/base-url metadata when answer metrics are disabled

## RAG Ranking Mode Switches

- `RAG_ORCHESTRATOR_V4=true` enables v4 route path (generalized ranking).
- `RAG_LEGACY_QUERY_HEURISTICS=false` (default) keeps legacy route in generalized mode:
  - no route-level query-intent boosts/fallback,
  - no retrieval-core `source_boost`,
  - no legacy how-to fallback sorting or SQL prefilter path.
- Generalized `/rag/query` now also uses bounded canonical rewrite fan-out for short/ambiguous KB questions:
  - at most 3 variants including the original query,
  - only deterministic lexical projections (`definition_focus`, `point_focus`, `fact_focus`, `keyword_focus`),
  - fused by stable chunk identity with bounded reciprocal-rank-style aggregation,
  - no LLM rewrite step and no corpus-specific synonym dictionary.
- Rollback only: set `RAG_LEGACY_QUERY_HEURISTICS=true` to temporarily restore both route-level query heuristics and retrieval-core legacy ranking behavior.
- `RAG_ORCHESTRATOR_V4=true` keeps generalized behavior regardless of `RAG_LEGACY_QUERY_HEURISTICS`.
- Apply quality gate from report artifacts (no DB run lookup):
  - `python scripts/rag_eval_quality_gate.py --run-report-json data/rag_eval_baseline/latest/baseline_v1.json --allow-no-baseline --print-json`
  - `python scripts/rag_eval_quality_gate.py --run-report-json <run_report.json> --baseline-report-json <baseline_report.json> --print-json`
- Produce baseline artifacts (JSON + Markdown):
  - `python scripts/rag_eval_baseline_runner.py --suite rag-general-v1 --label baseline_v1 --out-dir data/rag_eval_baseline`
  - outputs include timestamped reports under `data/rag_eval_baseline/runs/baseline_v1/`, stable snapshots under `data/rag_eval_baseline/latest/baseline_v1.{json,md}`, and append-only trend history in `data/rag_eval_baseline/trends/baseline_v1.jsonl`.
  - markdown reports include `Slice Summary`, `Source Families`, and `Security Scenarios` sections when that metadata is present in the run payload.
  - when answer metrics are enabled, markdown also includes answer/judge model metadata plus `Screening Summary`, `Security Summary`, `Case Failures`, and `Suspicious Events` sections.
  - canonical slice aliases such as `long_context`, `refusal_expected`, `direct_prompt_injection`, and `indirect_prompt_injection` normalize to the gate's canonical names before threshold evaluation.

## RAG Security Refusals

- Primary RAG answer paths now refuse several classes of malicious or overbroad queries before the LLM is called:
  - prompt/system-prompt leakage requests,
  - secret/credential disclosure requests,
  - unrelated private-message disclosure requests.
- Retrieved context is treated as untrusted data. If the final evidence pack contains instruction-like poisoned content (for example, “ignore previous instructions” / “reveal system prompt”), the route returns a deterministic refusal instead of sending that context to the model.
- Refusal wording must not echo exact secret terms from the query back into the answer, because local security eval treats that as a sensitive-term leak.
- This hardening is expected to improve `refusal_accuracy` / `security_resilience` in local eval without changing retrieval metrics; if those metrics regress, inspect the latest local run under `data/rag_eval_baseline/runs/`.

## Canonical Chunk Contract

- New runtime writes mirror canonical chunk fields into additive `knowledge_chunks` columns:
  - required-on-write fields: `chunk_hash`, `chunk_no`, `block_type`, `section_path_norm`, `token_count_est`, `parser_profile`
  - optional nullable fields: `page_no`, `char_start`, `char_end`, `parser_confidence`, `parser_warning`, `parent_chunk_id`, `prev_chunk_id`, `next_chunk_id`
- `backend/services/ingestion_service.py` is the primary canonical normalizer for runtime ingestion paths and now emits both `chunk_metadata` and `metadata_json` payloads with the same canonical contract.
- `shared/rag_system.py` auto-fills the required canonical fields for direct `add_chunk`/`add_chunks_batch` callers so legacy wiki/runtime paths do not leave `chunk_no` or JSON mirrors unset.
- Public Gitee wiki sync now tries wiki-specific clone targets (`.wiki.git`, `.wikis.git`, `/wikis.git`) with interactive git prompts disabled before falling back to HTML crawl; if live logs still show HTML fallback with only one page indexed, treat that as an ingest outage rather than a retrieval-only bug.
- Mixed embedding dimensions are isolated per KB in the legacy dense index path; if one KB is re-embedded with a new model dimension, other KBs remain searchable and only a KB-local dimension mismatch degrades to keyword search.
- `parser_warning` values are sanitized before persistence and must not retain URL credentials, auth headers, bearer tokens, or password/token/api-key key-value secrets.
- Additive migration safety is covered by a focused temporary-SQLite regression that exercises `migrate_database()` on a legacy `knowledge_chunks` table shape.
- Rollback stays additive:
  - stop populating canonical scalar fields if necessary,
  - leave the new columns in place,
  - do not perform destructive schema reversal.

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
  - use `included_in_context=true` plus `context_reason/context_anchor_rank` to distinguish retrieved anchors from support rows that entered the final prompt
  - if Qdrant errors spike, switch `RAG_BACKEND=legacy` and restart services
  - check `degraded_mode`/`degraded_reason` in diagnostics for dense-channel degradation markers
  - if the answer model times out or returns a provider transport/status error after retrieval succeeds, the user-facing response should degrade to a retrieval-only extractive fallback built from the selected evidence pack instead of showing the raw provider error; if raw transport text reaches the user, treat it as a regression in the fallback path

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
  - rerun with explicit slices for narrower diagnosis or inspect the `slice_groups` section from the gate output to isolate source-family vs security regressions
  - if CI fails before smoke tests, treat it as a fail-fast tooling/contract regression in the public-safe lane before investigating local-only eval workflows

- Bot transport disconnect noise:
  - transient Telegram transport/protocol disconnects (`NetworkError`, `RemoteProtocolError`, "Server disconnected without sending a response") are now warning-only and intentionally do not notify admins as critical incidents
  - inspect runtime logs first if users report intermittent disconnects; if the exception is not classified as transient, the usual admin critical-error notification path still applies

- Wiki ingest fail-fast:
  - treat `status=failed` from `/api/v1/ingestion/wiki-crawl` as a real ingest failure, not a successful partial sync
  - Gitee `HTML crawl` with only the root page or `0 pages / 0 chunks` is now classified as failed by design
  - expected operator guidance:
    - if `failure_reason=git_auth_required`, use repo access or send a wiki ZIP through the bot recovery flow
    - if `failure_reason=root_only_html_fallback` or `empty_wiki_result`, do not trust the created KB contents until recovery succeeds
  - bot-side recovery uses `wiki ZIP` restore, not generic archive document upload

- Admin log viewer:
  - `🪵 Логи сервисов` shows a bounded tail from `BOT_LOG_DIR` (`data/logs` by default)
  - output is redacted for tokens, auth headers, and credential-bearing URLs before being returned to chat
  - if viewer is empty, confirm log files exist under `BOT_LOG_DIR` and the service writes there

- Analytics failures:
  - inspect digest/import status endpoints
  - check scheduler and DB availability

## Rollback

- Revert to previous image/tag.
- Disable affected menu path in bot callbacks if needed.
- Keep DB schema backward-compatible for v1 rollback.
- For retrieval stack rollback, set `RAG_BACKEND=legacy` and restart backend/bot.
