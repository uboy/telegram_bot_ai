## Plan: Auto-Detect and Batch Document Upload with Report

Date: 2026-03-04
Status: In progress

### Implementation Checklist
- [x] Inspect current upload callbacks and document handler.
- [x] Replace `kb_upload` menu flow with direct “send files” flow.
- [x] Implement automatic file-type inference helper(s) in bot handler.
- [x] Implement single + media-group batch ingestion flow with job polling.
- [x] Implement pending list queue when KB is not selected.
- [x] Return consolidated report for successful/failed documents.

### Verification Checklist
- [x] Add regression tests for type inference and batch report behavior.
- [x] Run focused pytest for new/updated tests.
- [x] Run `py_compile` for changed Python files.
- [x] Add extra regression tests for explicit ingestion call and missing `job_id` fallback.

### Documentation Checklist
- [x] Update `SPEC.md` for new upload behavior and report requirement.
- [x] Add/Update design spec in `docs/design/*`.
- [x] Update `docs/REQUIREMENTS_TRACEABILITY.md`.
- [x] Update `docs/USAGE.md` (admin upload flow).

### Security/Policy Gates
- [x] No dependency changes expected.
- [x] Run secret scan before completion.
- [x] Produce review report in `coordination/reviews/*`.

## Plan: RAG Definition Intent + Admin Upload Navigation

Date: 2026-03-04
Status: Completed

### Implementation Checklist
- [x] Analyze current `rag_query` ranking and intent dispatch.
- [x] Add `DEFINITION` intent and boosts for definition markers.
- [x] Add `пункт N` reference boost for factoid questions.
- [x] Remove duplicate global upload button from admin menu.
- [x] Keep `admin_upload` callback as compatibility redirect.

### Verification Checklist
- [x] Add regression tests for definition-intent ranking and admin menu button set.
- [x] Run focused pytest for changed behavior.
- [x] Run `py_compile` for changed Python files.

### Documentation Checklist
- [x] Update `SPEC.md`.
- [x] Add design doc in `docs/design/`.
- [x] Update `docs/REQUIREMENTS_TRACEABILITY.md`.
- [x] Update `docs/USAGE.md`.

## Plan: Fix KB Creation State Machine Regression

Date: 2026-03-04
Status: In progress

### Implementation Checklist
- [x] Confirm user-reported reproduction path from logs.
- [x] Locate callback state assignment (`waiting_kb_name`) and text handler mismatch.
- [x] Implement `waiting_kb_name` branch in `frontend/bot_handlers.py`.
- [x] Keep behavior admin-only and robust for empty names/backend failure.

### Verification Checklist
- [x] Add automated regression test for `waiting_kb_name` -> `create_knowledge_base`.
- [x] Run focused pytest for bot text handler tests.
- [x] Run syntax check for changed files.

### Documentation Checklist
- [x] Evaluate whether SPEC/docs updates are required.
- [x] Update SPEC/design/traceability/usage docs for the bugfix.

### Security/Policy Gates
- [x] No new dependencies expected.
- [x] Run secret scan before completion.
- [x] Note missing policy validation scripts (`scripts/validate-review-report.ps1`, `scripts/validate-cycle-proof.ps1`).

## Plan: RAG Stack v2 Migration + KB Query UX Queue

Date: 2026-03-04
Status: Completed

### Implementation Checklist
- [x] Complete research snapshot for current ingestion/retrieval capabilities.
- [x] Define migration direction for high-quality retrieval without breaking current ingestion matrix.
- [x] Implement retrieval v2 in `backend/api/routes/rag.py`:
  - [x] add `FACTOID` intent and enhanced query hints.
  - [x] generalize keyword fallback for pointed factual/legal/metric queries.
  - [x] tune doc/context selection for non-howto factual queries.
- [x] Implement KB-search UX v2 in bot:
  - [x] progress bar message with auto-delete after response.
  - [x] queue multiple KB queries, process in-order.
  - [x] answer using `reply_to_message_id` under original user question.
  - [x] integrate queued pending queries when KB is selected from menu.
- [x] Add backend API smoke script for `/api/v1/rag/query`.

### Verification Checklist
- [x] Add/extend regression tests for RAG factoid retrieval.
- [x] Add/extend regression tests for KB query queue/progress behavior.
- [x] Run focused pytest suite for touched areas.
- [x] Run `py_compile` for touched Python modules.
- [x] Run secret scan.
- [x] Run policy docs gate.

### Documentation Checklist
- [x] Add design doc `docs/design/rag-stack-v2-migration-v1.md`.
- [x] Update `SPEC.md` for RAG v2 + KB queue/progress UX + API smoke script.
- [x] Update `docs/REQUIREMENTS_TRACEABILITY.md`.
- [x] Update `docs/USAGE.md` with new KB-search behavior and smoke script usage.
- [x] Add review report artifact.

### Security/Policy Gates
- [x] No dependency changes planned by default (if changed, run dependency security scan per policy).
- [x] Keep rollback plan documented for retrieval logic and UX behavior toggles.

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

## Plan: RAG Max Quality Architecture (v3)

Date: 2026-03-04
Status: Implementation iteration completed (qdrant + diagnostics), further benchmark/reindex phase pending

### Phase 1: Research + Architecture
- [x] Audit current retrieval/ingestion/quality pipeline and constraints.
- [x] Define target quality bar (retrieval accuracy + answer faithfulness + latency).
- [x] Produce architecture spec with data model, interfaces, rollout, rollback.
- [x] Wait for user approval on design (`APPROVED:v1`) before implementation.

### Phase 2: Implementation Decomposition (post-approval)
- [ ] Implement canonical chunk metadata normalization across loaders.
- [x] Implement retrieval orchestrator v3 (candidate channels + calibrated fusion).
- [x] Add retrieval decision logging and diagnostics endpoint.
- [ ] Add quality benchmark/eval harness for multi-intent and multi-format sets.
- [x] Deploy and integrate external hybrid retrieval backend as production target.
- [ ] Execute full reindex and production cutover to new stack.

### Verification Checklist
- [x] Unit tests for query classification, fusion, rerank, context packing.
- [x] Integration tests for `/api/v1/rag/query` on factoid/definition/howto/legal cases.
- [x] Regression tests for Telegram KB queue/progress behavior.
- [ ] Benchmarks with target thresholds (Recall@10, MRR@10, faithfulness pass rate, p95 latency).
- [ ] Validate rollback toggle in staging and production runbook.
- [x] `py_compile`, focused `pytest`, `scan_secrets`, `ci_policy_gate`.

### Documentation Checklist
- [x] Create design spec `docs/design/rag-max-quality-architecture-v1.md`.
- [x] Update `SPEC.md` after implementation deltas are finalized.
- [x] Update `docs/REQUIREMENTS_TRACEABILITY.md` with new AC mappings.
- [x] Update `docs/USAGE.md` and `docs/OPERATIONS.md` for new knobs/diagnostics.
- [x] Produce review report artifact for implementation cycle.
