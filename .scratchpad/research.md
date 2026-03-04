## Research: Auto-Detect Multi-Document Upload to KB

Date: 2026-03-04
Agent: codex (team-lead-orchestrator / architect phase)

### User Request
- Remove explicit file-type selection before upload.
- Bot must auto-detect uploaded document types.
- Support uploading multiple documents in one flow.
- Respect Telegram upload/file limits.
- Return report with successes/failures for validation.

### Findings
- Current admin flow still asks explicit type:
  - `frontend/bot_callbacks.py` (`kb_upload` -> `document_type_menu`).
- `handle_document` is incomplete and does not launch backend ingestion.
- Callback still references removed function `load_document_to_kb` (runtime mismatch risk).
- Backend supports async ingestion per file with job tracking:
  - `POST /ingestion/document` returns `job_id`.
  - `/jobs/{id}` exposes completion/failure.
- Existing loader manager supports many types by extension:
  - markdown/pdf/docx/xlsx/txt/json/chat/code/image and more.
- Telegram file size limits are already codified in `shared/asr_limits.py` via `get_telegram_file_max_bytes()` (20 MB) and can be reused for document guard.

### Design Direction
- Replace “select type first” UX with “send one or several files”.
- Infer file type from filename/mime in bot handler and send inferred type to backend.
- Implement batch processing with grouped summary report:
  - total accepted,
  - per-file success/failure,
  - reasons (size, unsupported type, job error, timeout).
- Support pending queue when KB is not selected yet.
- Preserve backwards compatibility for old `upload_type:*` callbacks where possible.

## Research: KB Creation Fails in Admin Panel

Date: 2026-03-04
Agent: codex (team-lead-orchestrator / architect phase)

### User Symptom
- In admin panel, after selecting `➕ Создать базу знаний` and entering a KB name (`МВП`, `MVP`), bot responds with `👋 Добро пожаловать!`.
- Returning to KB list shows old KB only; new KB is not created.

### Root Cause
- `frontend/bot_callbacks.py` sets `context.user_data['state'] = 'waiting_kb_name'` on callback `kb_create`.
- `frontend/bot_handlers.py::handle_text` has no branch for `state == 'waiting_kb_name'`.
- As a result, entered KB name falls through to default branch and calls `handle_start(...)`, which sends `👋 Добро пожаловать!`.

### Impact
- KB creation flow from Telegram admin UI is broken for all admins.
- Feature regression: state machine mismatch between callback and text handler.

### Fix Direction
- Add explicit `waiting_kb_name` branch in `handle_text` for admin users:
  - validate non-empty KB name,
  - call backend `create_knowledge_base`,
  - return success/error message and admin menu,
  - clear state after handling.
- Add regression test for `waiting_kb_name` flow.

# Research: AI Mode v2 (Telemetry + Context Memory + Progress UX)

Date: 2026-03-01
Agent: codex (team-lead-orchestrator / architect phase)

## User Request (new)
1. Confirm whether Open WebUI is currently supported.
2. For all requests sent to AI, store model/request metrics in DB.
3. Use metrics to predict request duration; if expected duration > 5s, show progress indicator and remove it after response.
4. Improve direct AI mode quality for weaker models (30b/70b/120b):
   - richer dialog context,
   - first answer must be concise,
   - ask clarifying question when input is ambiguous.
5. On re-entering AI mode, ask user to restore previous context or start a new dialog.
6. Persist/reload context from DB and compress memory due limited context window.

## Codebase Findings
- Open WebUI support exists and is production-wired:
  - `shared/ai_providers.py` contains `OpenWebUIProvider`.
  - Provider registration key: `open_webui`.
  - Env vars: `OPEN_WEBUI_BASE_URL`, `OPEN_WEBUI_API_KEY`, `OPEN_WEBUI_MODEL`.
  - Covered by `tests/test_ai_providers.py`.
- Direct AI mode is implemented in `frontend/bot_handlers.py` via state `waiting_ai_query`.
- Voice/audio handoff (`ASR -> AI`) exists for AI mode.
- Metrics/telemetry for AI requests are not persisted in DB now.
- Conversation memory for direct AI mode is not persisted; prompts are single-turn (`create_prompt_with_language(..., context=None)`).
- Current direct prompt says "Ответь подробно", which conflicts with desired concise-first behavior.

## Existing Risk Signals from User Transcript
- Out-of-order/mixed replies likely from overlapping in-flight AI requests per user.
- Very long responses still possible semantically (even if chunking prevents Telegram hard error).
- Weak models drift without strict prompt contract + compact structured memory.

## Architecture Implications
- Instrumentation should be centralized in `AIProviderManager.query/query_multimodal` to cover all AI callers.
- Session memory should be a dedicated feature service, not ad-hoc `context.user_data`.
- Progress UX should be ephemeral Telegram message, deleted on completion, with >5s predictor trigger.
- Need per-user concurrency guard for AI requests to avoid stale/late answer interleaving.

## Scope Decision
This is a non-trivial cross-cutting feature touching:
- frontend AI mode UX/state,
- shared provider layer telemetry,
- DB schema + migrations,
- prompt policy and memory compression,
- tests/spec/docs.
