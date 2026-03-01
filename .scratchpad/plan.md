# Plan: AI Mode v2 (Telemetry + Predictor + Context Sessions)

Date: 2026-03-01
Status: Draft for CC approval (design-first)

## Delivery Slices
1. Schema + telemetry core
- Add tables:
  - `ai_conversations`
  - `ai_conversation_turns`
  - `ai_request_metrics`
- Add indexes for `(provider, model, feature, created_at)` and `(user_telegram_id, updated_at)`.
- Extend `migrate_database()` in `shared/database.py` with safe additive migration blocks.
- Add `shared/ai_metrics.py` for write/read stats and ETA prediction.

2. Provider-layer instrumentation
- Wrap `AIProviderManager.query/query_multimodal`:
  - capture start/end time, status, provider/model, prompt/response sizes,
  - persist metric row for every request path (bot + backend jobs),
  - return response unchanged (no behavior break).
- Add predictor function:
  - input: provider/model/feature + prompt/context size,
  - output: `predicted_latency_ms` based on recent DB history (EWMA/median by bucket).

3. Direct AI mode session memory
- Add `shared/ai_conversation_service.py`:
  - start/reuse conversation,
  - append turns,
  - build compact context for prompt,
  - maintain rolling summary.
- Add model-aware context budgets:
  - defaults in config, optional per-model override.
- Keep recent turns verbatim + compressed summary of older turns.

4. Prompt policy for weak models
- Add dedicated prompt builder for direct AI mode:
  - concise-first first response,
  - clarification-first when ambiguous,
  - strict anti-verbosity limits on first turn,
  - include compressed memory + recent turns + current query.
- Keep existing RAG/web prompts unchanged.

5. Telegram UX flow
- On "🤖 Задать вопрос ИИ":
  - if previous session exists, ask:
    - restore context,
    - start new dialog.
- Add ephemeral progress message behavior:
  - show immediately if predicted > 5s;
  - else show after 5s timeout if still running;
  - animate/edit while waiting;
  - delete when answer arrives or fails.
- Add per-user in-flight guard (lock/request-id) to avoid mixed late replies.

6. Tests + docs/spec
- Tests:
  - new DB telemetry tests,
  - predictor tests,
  - AI-mode restore/new session tests,
  - concise-first/clarify-first prompt tests,
  - progress message lifecycle tests (shown/deleted),
  - concurrent request guard tests.
- Docs:
  - `SPEC.md` new AC for telemetry/predictor/session restore/progress UX.
  - `docs/REQUIREMENTS_TRACEABILITY.md` mapping updates.
  - `docs/USAGE.md` and `docs/OPERATIONS.md` behavior/config notes.

## Verification Plan
1. Unit tests for metrics + predictor + context compression services.
2. Existing AI-mode/voice/audio regressions must remain green.
3. Manual Telegram smoke:
- text AI query (short answer),
- ambiguous query (clarifying question),
- voice in AI mode (`ASR -> AI`),
- re-enter AI mode restore/new branch,
- long request progress indicator appears and is removed.

## Risks and Mitigations
- Risk: extra DB writes increase latency.
  - Mitigation: keep metric insert minimal and indexed; fallback non-blocking on logging failures.
- Risk: weak-model summary drift.
  - Mitigation: bounded summary format + keep last turns verbatim.
- Risk: progress message orphaning on exceptions.
  - Mitigation: `try/finally` cleanup and delete best-effort.
