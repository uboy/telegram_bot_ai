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

## Jobs

- `GET /jobs/{job_id}`
  - Response fields: `job_id`, `status`, `progress`, `stage`, `error`.

## RAG

- `POST /rag/query`
- `POST /rag/summary`
- `POST /rag/reload-models`

`/rag/summary` modes:
- `summary`
- `faq`
- `instructions`

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
