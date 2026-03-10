# API Reference (`/api/v1`)

## Auth Model

- Header: `X-API-Key: <BACKEND_API_KEY>`
- Public endpoints:
  - `GET /health`
  - `POST /auth/telegram`
- All other endpoint groups require API key.

## Health

- `GET /health`

## Auth / Users

- `POST /auth/telegram`
- `GET /users/`
- `POST /users/{user_id}/toggle-role`
- `DELETE /users/{user_id}`

## Knowledge Bases

- `GET /knowledge-bases/`
- `POST /knowledge-bases/`
- `GET /knowledge-bases/{kb_id}/sources`
- `POST /knowledge-bases/{kb_id}/clear`
- `DELETE /knowledge-bases/{kb_id}`
- `GET /knowledge-bases/{kb_id}/import-log`
- `GET /knowledge-bases/{kb_id}/settings`
- `PUT /knowledge-bases/{kb_id}/settings`

## Ingestion

- `POST /ingestion/web`
- `POST /ingestion/wiki-crawl`
- `POST /ingestion/wiki-git`
- `POST /ingestion/wiki-zip`
- `POST /ingestion/document`
- `POST /ingestion/image`
- `POST /ingestion/code-path`
- `POST /ingestion/code-git`

Most ingestion endpoints return `job_id` for async processing.

Wiki ingestion notes:
- `POST /ingestion/wiki-crawl` returns crawl stats for the selected wiki root.
- Wiki-crawl response includes `crawl_mode` (`git` or `html`) and `git_fallback_attempted` so callers can distinguish a successful git-based full sync from HTML crawl fallback.

## Jobs

- `GET /jobs/{job_id}`
  - Response fields: `job_id`, `status`, `progress`, `stage`, `error`.

## RAG

- `POST /rag/query`
- `GET /rag/diagnostics/{request_id}`
- `POST /rag/eval/run`
- `GET /rag/eval/{run_id}`
- `POST /rag/summary`
- `POST /rag/reload-models`

`/rag/summary` modes:
- `summary`
- `faq`
- `instructions`

`GET /rag/diagnostics/{request_id}` returns:
- request metadata (`intent`, `orchestrator_mode`, `retrieval_core_mode`, `hints`, `filters`, `latency_ms`, `backend_name`)
- degraded markers (`degraded_mode`, `degraded_reason`)
- top candidate diagnostics (`origin`, `channel`, `channel_rank`, `fusion_rank`, `fusion_score`, `rerank_delta`)
- final-context diagnostics (`included_in_context`, optional `context_rank`, `context_reason`, `context_anchor_rank`) so callers can tell which chunks actually entered the final prompt evidence pack
- support-only evidence rows may appear with `origin=channel=context_support` when the final prompt needed adjacent/section support that was not present in the raw retrieval top-N
- candidate trace contract keeps `origin`, `channel`, `channel_rank`, `fusion_rank`, and `fusion_score` non-null even for older persisted rows; `rerank_delta` may be `null` when no rerank signal exists

`POST /rag/eval/run` request body:
- `suite` (string)
- `baseline_run_id` (optional string)
- `slices` (optional list of slice names)

`GET /rag/eval/{run_id}` returns:
- run lifecycle (`queued|running|completed|failed`)
- run-level metrics summary
- per-slice metric rows (`recall_at_10`, `mrr_at_10`, `ndcg_at_10`)

## ASR

- `POST /asr/transcribe`
- `GET /asr/jobs/{job_id}`
- `GET /asr/settings`
- `PUT /asr/settings`

## Analytics

- `POST /analytics/messages`
- `POST /analytics/messages/batch`
- `GET /analytics/configs`
- `GET /analytics/configs/{chat_id}`
- `PUT /analytics/configs/{chat_id}`
- `DELETE /analytics/configs/{chat_id}`
- `POST /analytics/digests/generate`
- `GET /analytics/digests/{digest_id}`
- `GET /analytics/digests`
- `POST /analytics/search`
- `POST /analytics/qa`
- `POST /analytics/import`
- `GET /analytics/import/{import_id}`
- `GET /analytics/stats/{chat_id}`

## Error Model (Common)

- `400` validation/business error
- `401` invalid/missing API key (for protected routes)
- `404` entity/job not found
- `422` request schema validation error
- `500` unhandled internal error
