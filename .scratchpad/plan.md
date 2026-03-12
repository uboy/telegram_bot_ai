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

## Plan: RAG Search Improvement Program + Wiki Flow Consolidation

Date: 2026-03-07
Status: Draft for CC approval

### Research Checklist
- [x] Audit current RAG runtime path (`shared/rag_system.py`, `backend/api/routes/rag.py`).
- [x] Audit existing design/docs/reviews for RAG quality work.
- [x] Audit unfinished backlog `RAGQLTY-009..018`.
- [x] Audit wiki example URL flow and confirm what is already fixed.

### Implementation Checklist
- [ ] Finish `RAGQLTY-009`:
  - stabilize hybrid fusion and rerank selection boundaries,
  - review hidden ranking heuristics still living in `shared/rag_system.py`,
  - keep generalized behavior as the default path.
- [ ] Finish `RAGQLTY-010`:
  - assert retrieval diagnostics content/order in automated tests,
  - make ranking regressions visible before prompt generation.
- [ ] Finish `RAGQLTY-011` and `RAGQLTY-012`:
  - align RU/EN answer prompts to the same grounded direct-answer contract,
  - remove forced template headings from remaining prompt branches,
  - add format regressions.
- [ ] Finish `RAGQLTY-013` to `RAGQLTY-015`:
  - relax command sanitizer to token-level validation,
  - preserve context-backed URLs, including wiki/document links,
  - add positive/negative safety regressions.
- [ ] Finish `RAGQLTY-016` to `RAGQLTY-018`:
  - add end-to-end RAG regression suite over representative fixed corpora,
  - wire CI fail-fast gate,
  - document local/CI quality workflow.
- [ ] Add wiki follow-up:
  - consolidate or retire orphan `wiki_git_load` / `wiki_zip_load` callback branches,
  - keep the working `waiting_wiki_root` path intact,
  - add regression coverage for fallback mode visibility and failure handling.
- [ ] Near-ideal follow-up after `RAGQLTY`:
  - introduce richer canonical chunk/document structure in runtime,
  - upgrade parser fidelity for PDF/DOCX/web ingestion,
  - move context assembly to evidence-pack policy,
  - expand eval to multi-corpus/source-family slices.

### Verification Checklist
- [ ] Run focused `pytest` per atomic step.
- [ ] Run `python -m py_compile <changed_py_files>`.
- [ ] Run `python scripts/scan_secrets.py`.
- [ ] Run `python scripts/ci_policy_gate.py --working-tree`.
- [ ] For quality-impacting steps, run eval baseline + quality gate compare.

### Documentation Checklist
- [ ] Update `SPEC.md` only when runtime behavior changes begin.
- [ ] Update/add `docs/design/*` per atomic step.
- [ ] Update `docs/REQUIREMENTS_TRACEABILITY.md`.
- [ ] Update `docs/USAGE.md`, `docs/OPERATIONS.md`, `docs/TESTING.md`, `docs/API_REFERENCE.md` when behavior/contracts change.
- [ ] Produce review reports in `coordination/reviews/*` for each implementation step.

### Security/Policy Gates
- [ ] No dependency changes by default; if that changes, run dependency security scan per policy.
- [ ] Keep rollback per step explicit and feature-flag based where possible.
- [ ] Do not broaden logging to include secrets or full private URLs with credentials.

### Current execution source of truth
- `docs/design/rag-near-ideal-task-breakdown-v1.md`
- `coordination/tasks.jsonl` entries `RAGEXEC-001..018`

## Plan: Embedded Local RAG Quality-Evaluation System

Date: 2026-03-08
Status: Draft for CC approval

### Design Deliverables
- [x] Audit current retrieval-only eval flow and existing quality-gate artifacts.
- [x] Audit accessibility and risk profile of requested local data sources:
  - `test.pdf`
  - `open-harmony`
  - Telegram export
- [x] Produce dedicated design spec for embedded quality evaluation with Ollama-backed answer/judge workflow.

### Planned Implementation Slices
1. Dataset and fixture contract (`RAGEXEC-008`)
- [ ] Add versioned dataset v2 contract with source-family and answer-level fields.
- [ ] Define fixture manifest for committed-safe vs local-only corpora.
- [ ] Add negative/no-answer and noisy-context cases.
- [ ] Add adversarial security cases:
  - direct prompt injection queries,
  - poisoned document snippets,
  - system-prompt probe cases,
  - confidential-data leakage and redaction cases.
- [ ] Add contract tests for dataset schema and fixture manifest resolution.

2. Eval runner and trend artifacts (`RAGEXEC-009`)
- [ ] Extend eval runner to execute:
  - retrieval scoring,
  - answer generation via real RAG path,
  - judge scoring via Ollama.
- [ ] Persist per-run JSON/Markdown reports plus append-only trend history.
- [ ] Emit source-family breakdown and failure-mode summaries.
- [ ] Emit security breakdown and suspicious-event summaries:
  - injection resistance,
  - leakage-block behavior,
  - screening/quarantine outcomes,
  - prompt/context separation compliance.

3. Local test integration and fail-fast workflow (`RAGEXEC-010`)
- [ ] Add `pytest` coverage for:
  - dataset contract,
  - local source preparation contract,
  - quality gate/trend logic,
  - optional slow local E2E quality run.
- [ ] Keep CI-safe subset runnable without private local corpora.
- [ ] Keep full local suite runnable with Ollama and optional local source overrides.
- [ ] Add security regression lane for adversarial cases and observability artifacts.

4. Quality-driven ingestion/context improvement loop (`RAGEXEC-013..018`)
- [ ] Use source-family metrics to guide chunking/parser work for PDF, code/docs, and Telegram chat sources.
- [ ] Add context-assembly diagnostics and evidence-pack metrics for `RAGEXEC-016..017`.
- [ ] Finalize source-family thresholds and near-ideal gate in `RAGEXEC-018`.

### Verification Checklist
- [ ] Contract tests must validate dataset schema and artifact schema without Ollama.
- [ ] Local quality run must verify Ollama availability and fail loudly if the judge path is requested but unavailable.
- [ ] Baseline vs candidate comparison must produce machine-readable deltas and human-readable Markdown summary.
- [ ] Trend artifact must include:
  - dataset version,
  - source-family slices,
  - answer model id,
  - judge model id,
  - git revision or `unknown`,
  - dirty-working-tree marker.
- [ ] Security verification must include:
  - prompt-injection resistance metrics,
  - poisoned-document / indirect-injection cases,
  - system-prompt leak refusal cases,
  - sensitive-context minimization checks,
  - suspicious query/document/answer observability output.

### Documentation Checklist
- [x] Create design spec in `docs/design/`.
- [ ] On implementation start, update:
  - `SPEC.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`
  - `docs/TESTING.md`
  - `docs/OPERATIONS.md`
  - `docs/CONFIGURATION.md`

### Security and Data Safety
- [ ] Do not commit raw Telegram export.
- [ ] Use local-only cache/materialization for private corpora.
- [ ] Keep judge inputs bounded to case context and expected facts; do not dump unrelated private chat history into artifacts.
- [ ] Prefer sanitized committed fixture subsets for CI and review.
- [ ] Treat RAG security as a required gate:
  - ingestion-time screening for malicious documents,
  - explicit separation of system instructions / user query / retrieved context,
  - no system-prompt disclosure,
  - no arbitrary filesystem / production-KB scope during eval,
  - suspicious-event logs in run artifacts.

### Proposed Command Surface for Implementation Phase
- [ ] `pytest` fast contract lane for eval schemas and gate logic.
- [ ] `pytest -m rag_quality_local` slow local lane for full ingestion + Ollama judge.
- [ ] baseline/trend CLI wrapper around the same service logic for auditable reports.

### Approval Gate
- [ ] Wait for user CC on `docs/design/rag-embedded-quality-eval-system-v1.md` before changing code or backlog.

## Plan: Embedded Local RAG Quality-Evaluation + Security System v2

Date: 2026-03-08
Status: Draft for CC approval
Supersedes:
- same-day draft `Embedded Local RAG Quality-Evaluation System`
- design draft `docs/design/rag-embedded-quality-eval-system-v1.md` for implementation planning purposes

### Planning Objective
- Define one implementation roadmap that turns local quality measurement into a repeatable engineering loop:
  - ingest known corpora,
  - run real retrieval and answer generation,
  - judge outputs through Ollama,
  - compute source-family and security metrics,
  - compare against baselines,
  - record trend growth per run and per commit.

### Design Deliverables
- [x] Audit current eval, provider, ingestion, and local corpus state.
- [x] Define a dataset + source-manifest contract that can represent safe, local-only, adversarial, and refusal cases.
- [x] Define a source-family metric system and security metric system.
- [x] Define artifact storage and trend-reporting contract.
- [x] Produce a superseding design spec:
  - `docs/design/rag-embedded-quality-eval-security-system-v1.md`

### Backlog Alignment
1. `RAGEXEC-008`: dataset, fixture manifest, adversarial/security case contract
- [ ] Add dataset v2 schema with:
  - `source_family`,
  - `gold_facts`,
  - `required_context_entities`,
  - refusal expectations,
  - adversarial fields and expected observability flags.
- [ ] Add source manifest schema with:
  - path resolution mode,
  - sensitivity,
  - commit policy,
  - screening profile,
  - materialization/cache contract.
- [ ] Add committed-safe mini-fixtures and schema tests.
- [ ] Add CI-safe adversarial fixture subset.

2. `RAGEXEC-009`: evaluator extension, judge integration, and trend artifacts
- [ ] Extend `backend/services/rag_eval_service.py` to orchestrate:
  - retrieval scoring,
  - real answer path execution,
  - local Ollama judge scoring,
  - suspicious-event aggregation,
  - Markdown/JSON trend artifact generation.
- [ ] Extend baseline/gate scripts to compare:
  - aggregate metrics,
  - source-family slices,
  - security slices,
  - per-case failure classes.

3. `RAGEXEC-010`: local test workflow and gate integration
- [ ] Add fast contract tests that run without Ollama.
- [ ] Add slow local `pytest -m rag_quality_local` lane.
- [ ] Document required local setup:
  - Ollama endpoint,
  - answer/judge models,
  - external corpus path overrides,
  - artifact cleanup/retention.
- [ ] Add fail-fast semantics for:
  - missing local corpora,
  - unreachable Ollama judge when requested,
  - security gate regression.

4. `RAGEXEC-013..015`: ingestion hardening driven by new metrics
- [ ] Use source-family failures to prioritize:
  - PDF parser fidelity,
  - canonical chunk contract,
  - web/wiki/code structural normalization,
  - chat-export privacy and structure handling,
  - ingestion-time malicious-document screening.

5. `RAGEXEC-016..017`: context quality loop
- [ ] Add evidence-pack diagnostics and context-inclusion reasons.
- [ ] Gate improvements using:
  - `evidence_in_context_recall`,
  - `faithfulness`,
  - `noise_sensitivity`,
  - `sensitive_context_overexposure_rate`.

6. `RAGEXEC-018`: near-ideal thresholds
- [ ] Finalize hard gates per source family and security slice.
- [ ] Promote stable judge metrics from trend-only to hard-threshold where justified.

### Mandatory Metric Families
- [ ] Retrieval:
  - `recall_at_10`, `mrr_at_10`, `ndcg_at_10`, `source_hit_at_k`, `evidence_in_context_recall`, `context_entity_recall`
- [ ] Answer grounding:
  - `faithfulness`, `response_relevancy`, `answer_correctness`, `response_groundedness`, `exact_match`, `string_presence`
- [ ] Context quality:
  - `context_precision`, `context_recall`, `context_relevance`, `noise_sensitivity`, `evidence_pack_efficiency`
- [ ] Control/refusal:
  - `refusal_precision`, `refusal_recall`, `citation_validity`, `grounded_url_precision`, `grounded_command_precision`
- [ ] Security:
  - `prompt_injection_resistance`, `indirect_injection_resistance`, `system_prompt_leak_block_rate`, `secret_leak_block_rate`, `sensitive_context_overexposure_rate`, `screening_recall`, `screening_precision`, `suspicious_*_flag_recall`, `instruction_plane_separation_compliance`

### Verification Checklist For Implementation Phase
- [ ] `python -m py_compile <changed_py_files>`
- [ ] `.venv\Scripts\python.exe -m pytest -q tests/test_rag_eval_dataset_contract.py tests/test_rag_eval_service.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py`
- [ ] `.venv\Scripts\python.exe -m pytest -q -m rag_quality_local tests/test_rag_eval_local_e2e.py`
- [ ] `.venv\Scripts\python.exe scripts/rag_eval_baseline_runner.py --dataset tests/data/rag_eval_ready_data_v2.yaml --source-manifest tests/data/rag_eval_source_manifest_v1.yaml --label local-ollama`
- [ ] `.venv\Scripts\python.exe scripts/rag_eval_quality_gate.py --baseline-report-json <baseline> --run-report-json <candidate>`
- [ ] `python scripts/scan_secrets.py`
- [ ] `python scripts/ci_policy_gate.py --working-tree`

### Review and Documentation Checklist
- [ ] Produce an independent review artifact for each functional slice in `coordination/reviews/`.
- [ ] Update on implementation start:
  - `SPEC.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`
  - `docs/TESTING.md`
  - `docs/OPERATIONS.md`
  - `docs/CONFIGURATION.md`
- [ ] Update runbook docs with:
  - local corpus preparation,
  - Ollama answer/judge model setup,
  - baseline refresh policy,
  - trend interpretation rules,
  - security triage workflow for suspicious artifacts.

### Security and Data Safety Checklist
- [ ] Keep raw Telegram export local-only and gitignored.
- [ ] Require sanitization/materialization before any Telegram-derived fixture enters committed tests or artifacts.
- [ ] Keep allowlisted corpus roots and dedicated eval KB scope only.
- [ ] Fail the run if suspicious-document screening or prompt-leak/security artifact generation is silently skipped.
- [ ] Ensure reports do not dump raw private chat history, hidden prompts, or secrets.

### Approval Gate
- [ ] Wait for user CC on `docs/design/rag-embedded-quality-eval-security-system-v1.md` before changing code, backlog, or implementation contracts.

## 2026-03-09 Delta Plan: answer-metric loop and external-project ideas

1. Short-term implementation order
- [ ] Finish `RAGEXEC-013` approval and implement canonical chunk contract first.
- [ ] Implement `RAGEXEC-014..015` with explicit structural fields that can support:
  - paragraph/section lookup,
  - sibling merges,
  - parser-fidelity diagnostics.
- [ ] Implement `RAGEXEC-016..017` with evidence-pack assembly and context-inclusion diagnostics.

2. Local-only quality loop extension
- [ ] Extend the eval service from retrieval-only to answer-aware local runs.
- [ ] Keep committed fast tests Ollama-free.
- [ ] Add optional local judge path that uses existing provider config plus eval-specific Ollama overrides.
- [ ] Report both:
  - raw component metrics,
  - weighted rollups per source family and security slice.

3. New ideas to fold into backlog after `RAGEXEC-016`
- [ ] Add a dedicated slice for controlled query rewriting + multi-query retrieval.
- [ ] Add explicit list-coverage / exhaustive-list metrics to validate answer completeness on list-style questions.
- [ ] Add security-eval scenarios for:
  - direct prompt injection,
  - indirect prompt injection / poisoned document behavior,
  - unsupported-value requests,
  - system-prompt leakage attempts,
  - sensitive-context overexposure.

4. Minimal answer-metric implementation shape
- [ ] Phase 1:
  - heuristics for `answer_correctness`, `response_relevancy`, `clarity`, `safety`
  - citation/refusal validators
  - aggregate + per-slice deltas in local artifacts
- [ ] Phase 2:
  - optional Ollama judge for `faithfulness`, `response_groundedness`, `citation_validity`
  - deterministic judge prompts and strict JSON schema
- [ ] Phase 3:
  - promote stable answer/security metrics into local quality-gate thresholds once variance is understood

## 2026-03-10 Plan: BOTFOLLOW-001 transient error suppression and KB-create redirect

### Goal
- Reduce operational noise from transient Telegram/network disconnects without hiding real failures.
- Remove the extra navigation step after KB creation by landing admins directly in the created KB menu.

### Implementation checklist
- [x] Add `BOTFOLLOW-001` coordination entry and switch the active cycle contract to this bugfix.
- [x] Implement transient-error classification and notification suppression in `frontend/error_handlers.py`.
- [x] Update the `waiting_kb_name` success branch in `frontend/bot_handlers.py` to use `kb_actions_menu(created_id)`.
- [x] Add regression coverage for:
  - transient network/protocol errors do not call `notify_admins(...)`,
  - non-transient exceptions still notify admins,
  - KB create success returns the KB actions menu for the new KB id.
- [x] Run focused verification:
  - `python -m py_compile frontend/error_handlers.py frontend/bot_handlers.py tests/test_bot_error_handlers.py tests/test_bot_text_ai_mode.py`
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_bot_error_handlers.py tests/test_bot_text_ai_mode.py`
  - `python scripts/scan_secrets.py`
  - `python scripts/ci_policy_gate.py --working-tree`
- [x] Request independent review and store the report under `coordination/reviews/`.
- [x] Sync `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, and `docs/OPERATIONS.md`.

### Affected files
- `frontend/error_handlers.py`
- `frontend/bot_handlers.py`
- `tests/test_bot_text_ai_mode.py`
- `tests/test_bot_error_handlers.py` (new, planned)
- `SPEC.md`
- `docs/REQUIREMENTS_TRACEABILITY.md`
- `docs/OPERATIONS.md`
- `coordination/tasks.jsonl`
- `coordination/cycle-contract.json`
- `coordination/state/codex.md`

### Risks and controls
- Risk: an overly broad transient-error matcher could suppress actionable failures.
  - Control: keep the matcher narrow and add a regression proving non-transient exceptions still notify admins.
- Risk: post-create routing could break existing admin expectations or leave the state uncleared.
  - Control: keep the existing success text, clear `context.user_data["state"]`, and assert the new reply markup directly in tests.

### Cross-platform verification note
- Runtime logic is platform-neutral Python.
- Focused automated checks run on Windows here; syntax-level verification is still valid cross-platform because no shell-specific runtime behavior is introduced.

## 2026-03-10 Plan: session-scoped KB selection for KB search

### Goal
- Ensure KB search chooses/searches the correct KB:
  - multiple KBs -> explicit choice,
  - one KB -> auto-select,
  - re-entry into KB search -> selection resets.

### Implementation checklist
- [x] Add a dedicated search-session key such as `active_search_kb_id`.
- [x] Update KB-search entry points (`"🔍 Поиск в базе знаний"` and callback `search_kb`) to:
  - reset previous search session,
  - auto-select when exactly one KB exists,
  - prompt for KB choice when multiple KBs exist,
  - fail clearly when no KBs exist.
- [x] Update `_ensure_kb_or_ask_select(...)`, pending-query flow, and `kb_select:<id>` handling for `waiting_kb_for_query` to use `active_search_kb_id`.
- [x] Keep admin KB-management logic on the existing `kb_id` path so uploads/wiki/settings/clear/delete do not change.
- [x] Add focused regressions in `tests/test_bot_text_ai_mode.py` for:
  - multi-KB explicit prompt,
  - single-KB auto-select,
  - queueing with `active_search_kb_id`,
  - re-entry reset of the search-session KB.

### Verification checklist
- [x] `python -m py_compile frontend/bot_handlers.py frontend/bot_callbacks.py tests/test_bot_text_ai_mode.py`
- [x] `.venv\\Scripts\\python.exe -m pytest -q tests/test_bot_text_ai_mode.py`
- [x] `python scripts/scan_secrets.py`
- [x] `python scripts/ci_policy_gate.py --working-tree`

### Documentation checklist
- [x] Update `docs/design/bot-kb-search-session-scope-v1.md`.
- [x] Update `SPEC.md`.
- [x] Update `docs/REQUIREMENTS_TRACEABILITY.md`.
- [x] Update `docs/USAGE.md`.
- [ ] Update `docs/OPERATIONS.md` only if an operational note is actually needed.

### Risks and controls
- Risk: `kb_select:<id>` changes could break admin KB management.
  - Control: only use `active_search_kb_id` when the bot is in `waiting_kb_for_query`; leave normal KB management on `kb_id`.
- Risk: stale selected KB could still leak across sessions.
  - Control: clear `active_search_kb_id` inside `_reset_kb_query_state(...)` and cover this with regression tests.

### 2026-03-10 reviewer blocker follow-up
- [x] Replace the remaining `knowledge_base_menu(...)` prompts in KB-search flows with `knowledge_base_search_menu(...)`.
- [x] Clear search-session state when leaving into `main_menu`, `admin_menu`, or `admin_kb`.
- [x] Add regressions for search-only KB choice after typed query and for `admin_kb -> kb_select` not being hijacked by stale `waiting_kb_for_query`.
- [x] Re-run independent review on the fixed flow.

## 2026-03-10 Plan: wiki URL state isolation and open-harmony relevance

### Goal
- Fix the remaining live wiki-ingest ambiguity and validate open-harmony search quality on local-only corpus data.

### Implementation checklist
- [ ] Isolate `kb_wiki_crawl` from stale upload/document state in `frontend/bot_callbacks.py` and `frontend/bot_handlers.py`.
- [ ] Add focused bot regressions for wiki-flow state cleanup.
- [ ] Improve default wiki/markdown chunking if local open-harmony comparison shows a clear gain.
- [ ] Add focused config/chunking regressions for the chosen default.

### Verification checklist
- [ ] `python -m py_compile frontend/bot_callbacks.py frontend/bot_handlers.py shared/kb_settings.py tests/test_bot_wiki_callbacks.py tests/test_bot_text_ai_mode.py`
- [ ] `.venv\\Scripts\\python.exe -m pytest -q tests/test_bot_wiki_callbacks.py tests/test_bot_text_ai_mode.py`
- [ ] Local open-harmony ingest + query comparison across chunking modes
- [ ] `python scripts/scan_secrets.py`
- [ ] `python scripts/ci_policy_gate.py --working-tree`

### Local-only evaluation checklist
- [ ] Ingest the local open-harmony corpus into a temporary KB with `full` chunking and record query outputs.
- [ ] Ingest the same corpus into a temporary KB with candidate chunking and record query outputs.
- [ ] Compare retrieval/answer behavior for a small open-harmony query set derived from `tests/rag_eval.yaml`.

### 2026-03-10 local comparison result
- [x] Compare `full` vs `section` chunking on local `open-harmony.wiki.zip`.
- [x] Choose `section` as the new default for `wiki` / `markdown`.
- [x] Re-run independent review and close `BOTFOLLOW-003`.
## 2026-03-11 BOTFOLLOW-004

- [done] Confirm the live blocker from backend logs and isolate it to the wrong Gitee clone target.
- [done] Update `shared/wiki_git_loader.py` to use public wiki repo candidates with prompt-disabled git execution and add regressions.
- [done] Re-run focused verification and a local-only open-harmony ingest/query comparison to confirm the live-path quality improvement.

## 2026-03-11 BOTFOLLOW-005

- [x] Inspect current testing/smoke patterns and define an opt-in local-only open-harmony wiki smoke workflow.
- [x] Add a local-only helper script plus pytest wrapper that ingest the real corpus into temporary SQLite and validate retrieval/extractive answers.
- [x] Run the smoke test against the local open-harmony wiki ZIP via env-supplied path and record the result.
- [x] Optimize the helper so the local smoke run stays practical:
  - disable reranking by default,
  - use a single `rag_query()` pass,
  - support `RAG_OPENHARMONY_WIKI_TOP_K`,
  - make repeated direct runs idempotent with unique KB names.
- [x] Request independent review and decide whether BOTFOLLOW-005 closes as completed tooling with a documented product-quality failure signal, or rolls straight into a new retrieval-quality fix slice.
- [x] Close BOTFOLLOW-005 as completed local-only tooling and register `BOTFOLLOW-006` for the remaining open-harmony build/sync relevance issue.

## 2026-03-11 BOTFOLLOW-006

- [in_progress] Record the active follow-up scope in coordination/state and keep BOTFOLLOW-006 generic, without hardcoding to one wiki.
- [pending] Add a metadata-field retrieval channel in `shared/rag_system.py` so strong `doc_title / section_title / section_path / source_path` matches can enter the hybrid candidate set.
- [pending] Add focused regressions for metadata-field candidate rescue and procedural build/sync queries.
- [pending] Re-run the local open-harmony smoke harness and check whether the two build/sync cases move from `XFAIL` to `PASS`.
- [pending] Run `py_compile`, focused `pytest`, `scan_secrets`, `ci_policy_gate`, and request an independent review.

## 2026-03-11 Plan: wiki ingest fail-fast, recovery flow, and admin log aggregation

Date: 2026-03-11
Status: Draft for CC approval

### Scope slices
1. Wiki ingest result contract
- [ ] classify wiki ingest stages explicitly:
  - git clone/fetch,
  - html crawl,
  - archive recovery,
  - final corpus validation
- [ ] define failure conditions that must not be reported as success:
  - `0 pages / 0 chunks`,
  - root-only Gitee HTML fallback,
  - archive restore without recovered chunks
- [ ] expose structured failure reason and recovery hints to bot UI

2. Bot wiki recovery session
- [ ] extend bot session state with wiki-ingest context:
  - `wiki_root_url`,
  - `wiki_ingest_stage`,
  - `wiki_failure_reason`,
  - `awaiting_wiki_archive`
- [ ] after git/html failure, keep user inside the wiki flow instead of dropping into generic upload mode
- [ ] allow the user to provide credentials/auth config only when the detected failure actually requires it
- [ ] allow the user to upload a wiki ZIP as a recovery step using the original URL context

3. Wiki archive restore path
- [ ] keep generic document archive upload unchanged
- [ ] add a separate wiki-archive ingest path in the bot UI/session
- [ ] ensure uploaded ZIP goes through `backend_client.ingest_wiki_zip(...)`
- [ ] ensure original wiki URL is used to restore page links and metadata

4. Admin log aggregation
- [ ] design a backend API for recent service-log slices
- [ ] define safe scope:
  - recent lines only,
  - redacted secrets,
  - bounded size
- [ ] add admin-panel entry to inspect logs for debugging ingestion/runtime failures

### Verification checklist for implementation phase
- [ ] add bot regressions for:
  - explicit wiki failure instead of false success
  - archive-recovery prompt after failed wiki ingest
  - wiki ZIP recovery path distinct from generic document upload
- [ ] add backend/service tests for:
  - fail-fast corpus validation thresholds
  - structured wiki ingest error payloads
  - log aggregation API bounds/redaction
- [ ] run:
  - `python -m py_compile <changed_py_files>`
  - focused `pytest`
  - `python scripts/scan_secrets.py`
  - `python scripts/ci_policy_gate.py --working-tree`

### Documentation checklist for implementation phase
- [ ] update `SPEC.md`
- [ ] update `docs/REQUIREMENTS_TRACEABILITY.md`
- [ ] update `docs/USAGE.md`
- [ ] update `docs/OPERATIONS.md`
- [ ] add/refresh design docs for wiki-ingest contract and admin log viewer

### Risks and controls
- Risk: asking for credentials too early or too often will overcomplicate the flow.
  - Control: credentials/auth prompt only after a concrete auth-classified git failure.
- Risk: aggregated logs can leak secrets.
  - Control: strict redaction, bounded line counts, admin-only access.
- Risk: mixing wiki archive restore with generic document upload can regress current upload UX.
  - Control: keep a separate bot state and explicit backend method for wiki ZIP recovery.
