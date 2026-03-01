# Feature Design Spec: Ask-AI Telemetry, Context Sessions, and Progress UX (v1)

## 1) Summary
This design upgrades direct AI mode to be stable for weaker models (30b/70b/120b), observable, and user-friendly:
- persist AI request metrics for all `ai_manager` calls,
- predict request duration from historical metrics,
- show ephemeral progress indicator for long requests (>5s) and remove it on completion,
- persist AI conversation sessions with restore/new choice on re-entry,
- compress context memory to fit limited model windows,
- enforce concise-first and clarification-first prompt behavior.

## 2) Current State
- Open WebUI is already supported through `OpenWebUIProvider` in `shared/ai_providers.py` and env vars `OPEN_WEBUI_*`.
- Direct AI mode currently uses mostly single-turn prompts (`create_prompt_with_language(..., context=None)`).
- No DB telemetry for AI latency/size metrics.
- No persistent conversation memory for direct AI mode.
- No long-request progress UX driven by predictor.

## 3) Goals and Non-Goals
### Goals
1. Telemetry: every AI request has a DB metric record.
2. Predictor: estimate expected duration before request dispatch.
3. UX: if request is expected to exceed 5s, show wait/progress message; remove it after completion.
4. Memory: maintain conversation context across entries with restore/new branch.
5. Prompt quality: first response concise; ambiguous requests trigger clarifying question first.
6. Keep existing non-AI flows unchanged.

### Non-Goals
- Replacing provider implementations or transport protocols.
- Introducing streaming tokens in this version.
- Full dialog memory for RAG analytics jobs (only direct AI mode requires session UX).

## 4) Architecture Overview
Add three shared services:
- `shared/ai_metrics.py`: telemetry persistence + latency prediction.
- `shared/ai_conversation_service.py`: session lifecycle, turn storage, context assembly/compression.
- `shared/ai_prompt_policy.py`: direct-AI prompt contract (concise-first, clarify-first, weak-model friendly format).

Integration points:
- `shared/ai_providers.py` (`AIProviderManager.query/query_multimodal`):
  - wrap each call with start/end instrumentation and metric write.
- `frontend/bot_handlers.py` + `frontend/bot_callbacks.py`:
  - session restore/new branch on entering AI mode,
  - call predictor before AI request,
  - render/remove progress message,
  - save user/assistant turns.

## 5) Data Model
Add ORM models and migration blocks in `shared/database.py`.

### `ai_conversations`
- `id` (PK)
- `user_telegram_id` (indexed)
- `provider_name`
- `model_name`
- `status` (`active|archived`)
- `title` (nullable)
- `summary_text` (rolling compressed memory)
- `summary_version` (int)
- `last_activity_at`
- `created_at`, `updated_at`

Indexes:
- `(user_telegram_id, last_activity_at DESC)`
- `(status, updated_at DESC)`

### `ai_conversation_turns`
- `id` (PK)
- `conversation_id` (FK -> `ai_conversations.id`, indexed)
- `turn_index` (int)
- `role` (`user|assistant|system`)
- `content` (text)
- `content_chars` (int)
- `content_tokens_est` (int)
- `created_at`

Constraints:
- unique `(conversation_id, turn_index)`

### `ai_request_metrics`
- `id` (PK)
- `request_id` (uuid-ish string, unique)
- `created_at`
- `feature` (`ask_ai_text|ask_ai_voice|ask_ai_audio|rag_fallback|digest|...`)
- `user_telegram_id` (nullable, indexed)
- `conversation_id` (nullable, indexed)
- `provider_name`
- `model_name`
- `request_kind` (`text|multimodal`)
- `prompt_chars`
- `prompt_tokens_est`
- `context_chars`
- `context_tokens_est`
- `history_turns_used`
- `predicted_latency_ms` (nullable)
- `latency_ms`
- `response_chars`
- `response_tokens_est`
- `status` (`ok|error|timeout`)
- `error_type` (nullable)
- `error_message` (nullable, truncated)

Indexes:
- `(provider_name, model_name, feature, created_at DESC)`
- `(status, created_at DESC)`

## 6) Predictor Design
Function:
- `predict_latency_ms(provider, model, feature, prompt_tokens_est, context_tokens_est) -> int`

Strategy (safe and simple):
1. Pull recent successful metrics for exact `(provider, model, feature)`, limit N=200.
2. Bucket by `total_tokens_est = prompt_tokens_est + context_tokens_est` (e.g., 256 token buckets).
3. Choose nearest populated bucket median latency.
4. Fallback chain:
   - same provider+model across features,
   - global provider median,
   - default `3000ms`.
5. Clamp result into sane range (e.g., 500..120000ms).

Decision rule:
- if predicted > 5000ms -> show progress message immediately.
- else start delayed watchdog; show progress only if request still running after 5s.

## 7) Context Session and Compression
Session entry:
1. User presses `🤖 Задать вопрос ИИ`.
2. If active recent session exists (configurable TTL, default 24h), ask:
   - `♻️ Восстановить контекст`
   - `🆕 Новый диалог`
3. Save selected `conversation_id` in `context.user_data`.

Context assembly:
- Always include:
  - compact session summary,
  - last `K` turns verbatim (default 4-6),
  - current user query.

Compression policy:
- Model context budget from config:
  - `AI_CONTEXT_BUDGET_TOKENS_DEFAULT`
  - optional `AI_CONTEXT_BUDGETS_JSON` per model override.
- If assembled context exceeds budget:
  - shrink older raw turns into updated `summary_text`,
  - keep only newest turns verbatim.
- Summary format is strict bullets:
  - goals,
  - established facts,
  - decisions/preferences,
  - open questions.

## 8) Prompt Policy (Direct AI Mode)
Introduce dedicated prompt builder for direct AI mode (do not modify RAG prompt builder).

Contract:
1. First assistant response must be concise:
   - default max 4 short sentences or max ~120 words.
2. If query is ambiguous or underspecified:
   - ask exactly one clarifying question first,
   - avoid long explanation before clarification.
3. If user asks "подробнее"/"expand", allow longer follow-up.
4. Use conversation context blocks from session summary + recent turns.

Output guardrails:
- Prefer short structured output (answer + optional clarification).
- Set provider-specific output caps where supported (`max_tokens`/equivalent).
- Keep Telegram-safe send path (`reply_html_safe`) as final safety net.

## 9) Progress Indicator UX
Telegram has no native progress bar, use an ephemeral status message:
- text variants like `⏳ Обрабатываю запрос...` with animated dots/spinner via `edit_text`.
- store `progress_message_id`.
- on success or failure:
  - delete progress message in `finally`,
  - send final answer/error normally.

Chat cleanliness:
- progress message is temporary only and should not remain in history after completion.

## 10) Concurrency Guard
Problem: overlapping requests from one user can produce mixed late answers.

Design:
- per-user in-flight lock + request id in `context.user_data`.
- if a second request arrives while first in flight:
  - either reject with short notice,
  - or queue (v1: reject for simplicity and safety).
- only release lock after full completion and cleanup.

## 11) Backward Compatibility
- Existing direct AI, voice/audio handoff, and chunk splitting remain.
- Open WebUI compatibility is unchanged (still via OpenAI-compatible endpoint).
- If telemetry write fails, request handling must continue (best-effort logging).

## 12) Files Planned for Implementation
- `shared/database.py`
- `shared/ai_providers.py`
- `shared/config.py`
- `shared/ai_metrics.py` (new)
- `shared/ai_conversation_service.py` (new)
- `shared/ai_prompt_policy.py` (new)
- `frontend/bot_handlers.py`
- `frontend/bot_callbacks.py`
- `frontend/templates/buttons.py` (if new session-choice buttons are needed)
- tests:
  - `tests/test_bot_text_ai_mode.py`
  - `tests/test_bot_voice.py`
  - `tests/test_bot_audio.py`
  - `tests/test_ai_providers.py`
  - new tests for metrics/predictor/session memory.

## 13) Verification Plan
Automated:
- metrics persistence on success/error,
- predictor fallback behavior,
- progress message shown/deleted in >5s path,
- restore/new session branch behavior,
- concise-first and clarification-first prompt behavior,
- non-regression for ASR->AI handoff and long message chunking.

Manual:
- ask AI text flow,
- voice in AI mode,
- re-enter AI mode restore/new path,
- intentional long request to observe progress cleanup.

## 14) Rollout and Rollback
Rollout:
1. ship schema + instrumentation,
2. enable session memory + prompt policy,
3. enable predictor-driven progress UX.

Rollback:
- feature flags/env toggles:
  - disable predictor UX,
  - disable session restore prompt,
  - fallback to current single-turn direct prompt.
- schema additions are additive and safe to keep.
