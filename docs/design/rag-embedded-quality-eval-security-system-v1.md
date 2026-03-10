# Design: Embedded Local RAG Quality-Evaluation + Security System v1

Date: 2026-03-08
Owner: codex (architect)
Status: APPROVED FOR IMPLEMENTATION (`APPROVED:v1`)

Supersedes for implementation planning:
- `docs/design/rag-embedded-quality-eval-system-v1.md`

Related artifacts:
- `docs/design/rag-quality-metrics-contract-v1.md`
- `docs/design/rag-eval-ready-corpus-v1.md`
- `docs/design/rag-eval-baseline-runner-v1.md`
- `docs/design/rag-eval-threshold-gate-workflow-v1.md`
- `docs/design/rag-near-ideal-task-breakdown-v1.md`
- `.scratchpad/research.md`
- `.scratchpad/plan.md`

## 1. Summary

### Problem statement
The repository already has a retrieval-focused RAG evaluation path, but it is not sufficient for driving the project to near-ideal quality. It does not yet evaluate the full answer path, it does not use the real mix of uploaded knowledge sources that matter to the user, it cannot show quality growth over time in a source-family-aware way, and it does not treat RAG security as a measured product requirement.

### Goals
- Add a repeatable local evaluation system that exercises the real RAG path end to end:
  - ingestion,
  - retrieval,
  - context assembly,
  - answer generation,
  - safety filtering.
- Use real project source families as evaluation inputs:
  - repo `test.pdf`,
  - local `open-harmony` corpus,
  - local Telegram chat export.
- Reuse the existing provider stack and support local Ollama-backed answer and judge roles.
- Produce per-run JSON and Markdown artifacts plus append-only trend history so quality movement can be compared across implementation slices and commits.
- Make source family, query shape, and security scenario first-class slices in both reports and gates.
- Make security a required evaluated outcome, not a later hardening note:
  - prompt injection,
  - indirect prompt injection / RAG poisoning,
  - confidential-data leakage,
  - system-prompt leakage,
  - ingestion-time screening,
  - instruction-plane separation,
  - least-privilege corpus access,
  - suspicious-behavior observability.

### Non-goals
- Replacing the production RAG stack in this design cycle.
- Adding cloud-hosted evaluation services.
- Committing raw Telegram export or other private corpora into the repository.
- Final threshold tuning before baseline runs exist.
- Solving all ingestion quality issues inside the design phase itself.

## 2. Scope Boundaries

### In scope
- Dataset and source-manifest contract for repo-safe synthetic/public fixtures plus developer-local corpora resolved through env overrides.
- Local fixture preparation and ingestion/eval workflow.
- Answer-level metric system, including lecture metrics and project-specific extensions.
- Ollama configuration contract for answer and judge roles.
- Artifact storage and trend-reporting contract.
- Security controls and security metrics for the full evaluation loop.
- Phased implementation plan aligned with `RAGEXEC-008..018`.

### Out of scope
- Runtime implementation changes in this design turn.
- Dependency changes.
- Production rollout of new security enforcement beyond what the future implementation will measure.
- New networked observability platforms such as LangSmith.

## 3. Assumptions and Constraints
- The project is design-first for non-trivial work; implementation begins only after CC approval.
- No source code, dependency, or configuration changes are part of this turn.
- Existing provider/config path should be reused:
  - `shared/ai_providers.py`
  - `shared/config.py`
  - `env.template`
  - `docs/CONFIGURATION.md`
- The evaluation system must work locally without external cloud APIs.
- The evaluator must measure the real product path rather than a simplified mock prompt flow.
- Telegram export is private and must remain local-only.
- `open-harmony` is available only through a developer-local path and must be resolved through an env override, not a hard-coded path.
- Current ingestion defaults in `shared/kb_settings.py` remain a known quality risk:
  - `web`, `wiki`, and `markdown` use very large `full` chunks by default.
- Current PDF parser availability may vary by environment; artifacts must record parser provenance and runs must fail clearly when the configured parser path is unavailable.

## 4. Architecture

### 4.1 Components and responsibilities
1. `Eval Dataset Contract`
- Defines case-level expectations:
  - query,
  - source family,
  - expected evidence,
  - answer/refusal contract,
  - security expectation,
  - expected observability flags.

2. `Source Manifest Resolver`
- Resolves committed-safe vs local-only fixture paths.
- Enforces allowlisted roots and sensitivity policy.
- Handles materialization of local-only corpora into a gitignored cache.

3. `Fixture Screening Layer`
- Screens source documents before they enter the eval KB.
- Produces `accepted`, `flagged`, or `quarantined` verdicts.
- Emits suspicious-document metadata for later reporting.

4. `Eval KB Builder`
- Builds or reuses a dedicated eval KB/collection for the selected manifest hash.
- Uses the same ingestion routes/services as production.

5. `Retrieval Evaluator`
- Runs the real retrieval path.
- Captures candidate diagnostics and computes retrieval metrics.

6. `Answer Evaluator`
- Runs the real answer path.
- Captures final answer, final context pack, selected source references, safety flags, and answer-level runtime.

7. `Judge Evaluator`
- Calls a pinned Ollama judge model with a strict rubric prompt and deterministic settings.
- Computes answer-level metrics and returns per-case justifications.

8. `Artifact Writer and Trend Reporter`
- Writes per-run JSON and Markdown artifacts.
- Maintains append-only trend history.
- Produces aggregate, per-slice, and per-case delta reports.

9. `Quality Gate`
- Consumes current run plus selected baseline.
- Applies hard thresholds and baseline-delta policies.
- Fails on missing mandatory artifacts, missing required slices, or security regressions.

### 4.2 High-level data flow
1. Load dataset v2 and source manifest.
2. Resolve fixture paths from repo, env overrides, or generated local cache.
3. Screen each fixture before ingestion and record verdicts.
4. Build the eval KB through normal ingestion services.
5. For each case:
   - run retrieval,
   - record retrieval diagnostics,
   - run answer generation through the real RAG path,
   - run judge scoring when enabled,
   - compute retrieval, answer, control, and security metrics,
   - collect suspicious-event flags.
6. Aggregate metrics by:
   - overall,
   - source family,
   - query type,
   - language,
   - security scenario.
7. Write JSON and Markdown reports plus append trend history.
8. Compare current run against baseline and enforce gates.

## 5. Interfaces and Contracts

### 5.1 Public command surface
Implementation should extend the existing Python entrypoints rather than create a parallel toolchain.

Planned command surface:
- `.venv\Scripts\python.exe scripts/rag_eval_baseline_runner.py --dataset tests/data/rag_eval_ready_data_v2.yaml --source-manifest tests/data/rag_eval_source_manifest_v1.yaml --label <label>`
- `.venv\Scripts\python.exe scripts/rag_eval_quality_gate.py --baseline-report-json <baseline> --run-report-json <candidate>`
- `.venv\Scripts\python.exe -m pytest -q tests/test_rag_eval_dataset_contract.py tests/test_rag_eval_service.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py`
- `.venv\Scripts\python.exe -m pytest -q -m rag_quality_local tests/test_rag_eval_local_e2e.py`

Cross-platform note:
- The implementation must keep the underlying Python entrypoints platform-neutral so Linux/macOS CI can invoke the same scripts with `python3`.

### 5.2 Dataset contract
File: `tests/data/rag_eval_ready_data_v2.yaml`

Required fields per case:
- `id`
- `query`
- `source_family`
- `expected_sources`
- `expected_snippets`
- `expected_answer_mode`
  - `grounded_answer`
  - `refusal`
- `security_expectation`
  - `normal`
  - `refuse_injection`
  - `refuse_prompt_leak`
  - `redact_sensitive`
  - `flag_poisoned_context`
- `tags`

Optional fields:
- `gold_answer`
- `gold_facts`
- `required_context_entities`
- `allowed_urls`
- `allowed_commands`
- `noise_fixture_ids`
- `must_cite_sources`
- `attack_type`
  - `none`
  - `direct_prompt_injection`
  - `indirect_prompt_injection`
  - `prompt_leak_probe`
  - `secret_leak_probe`
  - `access_scope_probe`
- `redacted_terms`
- `required_flags`
- `notes`

Dataset invariants:
- Every case maps to at least one fixture in the source manifest.
- Every refusal case declares why a refusal is expected.
- Every adversarial case declares:
  - `attack_type`,
  - `security_expectation`,
  - required observability flags.
- No committed case may embed or quote real developer-local corpus text.

### 5.3 Source manifest contract
File: `tests/data/rag_eval_source_manifest_v1.yaml`

Required fields per fixture:
- `fixture_id`
- `source_family`
  - `pdf`
  - `open_harmony_docs`
  - `open_harmony_code`
  - `telegram_chat`
  - reserved later: `wiki`
- `path_mode`
  - `repo_relative`
  - `env_override`
  - `generated_local_cache`
- `default_path`
- `required`
- `sanitized`
- `commit_allowed`
- `sensitivity`
  - `public`
  - `internal`
  - `confidential`
- `screening_profile`
  - `default`
  - `strict_private`
  - `adversarial_fixture`
- `ingest_kind`
  - `document`
  - `code_path`
  - `chat_export`
  - reserved later: `web`, `wiki`

Optional fields:
- `checksum`
- `notes`
- `prepare_step`

Manifest invariants:
- Local-only fixtures cannot be `commit_allowed=true`.
- `confidential` fixtures must not generate raw-text artifacts outside gitignored local storage.
- Fixtures with `screening_profile=adversarial_fixture` must participate in at least one security scenario.

### 5.4 Internal module boundaries
Planned internal service responsibilities:
- `backend/services/rag_eval_service.py`
  - orchestrates case execution and aggregation.
- `scripts/rag_eval_baseline_runner.py`
  - thin wrapper over the service.
- `scripts/rag_eval_quality_gate.py`
  - thin wrapper over run/baseline comparison.

Planned internal function boundaries:
- `load_eval_dataset(dataset_path) -> EvalDataset`
- `load_source_manifest(manifest_path) -> SourceManifest`
- `resolve_fixture_paths(manifest, env) -> ResolvedFixtures`
- `screen_fixture(fixture, profile) -> ScreeningVerdict`
- `materialize_local_fixture_subset(fixture, cache_dir) -> MaterializedFixture`
- `prepare_eval_kb(fixtures, config) -> EvalKBHandle`
- `run_retrieval_case(case, kb_handle) -> RetrievalCaseResult`
- `run_answer_case(case, kb_handle, answer_model) -> AnswerCaseResult`
- `judge_answer_case(case, answer_result, judge_model) -> JudgeCaseResult`
- `aggregate_eval_run(case_results) -> EvalRunReport`
- `write_eval_artifacts(report, out_dir) -> ArtifactPaths`
- `compare_eval_runs(baseline, candidate, gate_config) -> GateDecision`

### 5.5 Error handling strategy
- Missing required fixture:
  - fast contract tests fail immediately.
  - local full run fails with a clear fixture id and required env var/path.
- Screening failure:
  - `quarantined` fixtures must not be ingested unless the case explicitly expects quarantine behavior.
- Ollama unavailable:
  - if judge lane is requested, fail loudly rather than silently downgrading.
  - fast schema/tests must still run without Ollama.
- Missing required slices/metrics/artifacts:
  - quality gate fails.
- Artifact write failure:
  - run fails because trend visibility is part of the feature contract.

## 6. Data Model Changes and Migrations

### Required file-based artifacts
Committed inputs:
- `tests/data/rag_eval_ready_data_v2.yaml`
- `tests/data/rag_eval_source_manifest_v1.yaml`
- optional synthetic or public-safe fixtures under `tests/fixtures/rag_eval/`

Generated local artifacts:
- `data/rag_eval_local/fixtures/...`
- `data/rag_eval_local/kb_cache/...`
- `data/rag_eval_baseline/runs/<label>/<timestamp>_<run_id>.json`
- `data/rag_eval_baseline/runs/<label>/<timestamp>_<run_id>.md`
- `data/rag_eval_baseline/latest/<label>.json`
- `data/rag_eval_baseline/latest/<label>.md`
- `data/rag_eval_baseline/trends/<label>.jsonl`

### JSON report schema requirements
Every run artifact must include:
- `run_id`
- `dataset_version`
- `source_manifest_version`
- `answer_model`
- `judge_model`
- `effective_ollama_base_url`
- `git_sha`
- `git_dirty`
- `started_at`
- `finished_at`
- `fixture_summary`
- `screening_summary`
- `metrics`
- `slice_metrics`
- `security_summary`
- `case_failures`
- `suspicious_events`

### Migrations
- No database migration is required in the first implementation phase.
- Trend and baseline history remain file-based by default.
- If later the project wants DB-backed run history or alerting, that should be a separate design.

## 7. Edge Cases and Failure Modes
- `open-harmony` path changes between sessions:
  - resolved through env override, not hard-coded paths.
- Telegram export is missing, partially exported, or too large:
  - materialization step must fail with targeted guidance and must not write oversized raw reports.
- PDF parser implementation differs by environment:
  - report must record parser used and fail clearly if the chosen parser path is unavailable.
- Local run uses the same model for answer and judge:
  - allowed but recorded explicitly as a lower-confidence setup.
- Retrieved malicious document attempts to override instructions:
  - answer must not obey it; report must surface indirect injection resistance verdict.
- User asks to reveal system prompt:
  - answer must refuse and report prompt-leak block behavior.
- Sensitive Telegram content is relevant to one case but not another:
  - answer context must include only minimal required evidence; overexposure must count against the run.
- Screening false positives:
  - precision metric and manual review notes must make them visible.
- Missing trend baseline:
  - first baseline creation is allowed as a dedicated baseline run, but later comparisons must fail if baseline is required and missing.

## 8. Security Requirements

### 8.1 Trust boundaries
- User query is untrusted.
- Retrieved documents are untrusted data, even if they come from an indexed knowledge source.
- Judge prompts are separate from answer prompts and must not leak hidden system instructions or unrelated private context.
- Local corpora outside the repo are sensitive by default unless manifest marks them `public`.

### 8.2 Mandatory security controls
1. Ingestion-time screening
- Screen documents for:
  - instruction-like override text,
  - exfiltration prompts,
  - prompt-leak bait,
  - secrets/credential patterns,
  - suspicious encoded or hidden text patterns.
- Store verdict:
  - `accepted`
  - `flagged`
  - `quarantined`

2. Instruction-plane separation
- Prompt packaging must keep separate sections for:
  - system instructions,
  - user question,
  - retrieved context,
  - judge rubric.
- Retrieved context must be treated as evidence, not instructions.

3. Least privilege
- Eval runner resolves fixtures only from:
  - repo fixtures,
  - env-overridden local corpora,
  - generated local cache.
- No arbitrary path scanning.
- No production KB access.
- Ollama target should default to a local base URL and require explicit override for anything else.

4. Sensitive-data minimization
- Only minimal required evidence may enter context for private cases.
- Raw Telegram export text must not be copied into committed reports or trend history.
- Sensitive terms expected to remain hidden must be modeled explicitly in case contracts.

5. Leak blocking
- The system must refuse to reveal:
  - system prompts,
  - hidden instructions,
  - credentials,
  - unrelated private chat content.

6. Observability
- Each run must emit counts and case ids for:
  - suspicious queries,
  - suspicious documents,
  - suspicious answers,
  - screening outcomes,
  - prompt-leak blocks,
  - secret-leak blocks.

### 8.3 Dependency and logging policy
- No new dependencies without explicit approval.
- Logs and artifacts must not include secrets, raw tokens, or private chat dumps.
- Effective model ids and local Ollama base URL may be logged in artifacts, but credentials must never be stored.

## 9. Performance Requirements and Limits
- Fast contract lane must not require Ollama or private corpora.
- Full local quality run may be slower, but must support:
  - source-family filtering,
  - slice filtering,
  - cached fixture materialization,
  - cached eval KB reuse keyed by manifest hash when safe.
- Expected complexity:
  - retrieval and answer scoring scale linearly with number of cases,
  - screening scales linearly with fixture size,
  - trend append is O(1) per run.
- Reports must store summaries and bounded excerpts, not full corpora, to avoid artifact explosion.
- Latency metrics must be collected:
  - `answer_latency_ms`
  - `judge_latency_ms`

## 10. Observability

### Required artifact visibility
- Aggregate metrics.
- Per-slice metrics by source family, query type, language, and security scenario.
- Per-case failure reasons.
- Screening summary by verdict and fixture id.
- Suspicious-event summary by class and case id.
- Run metadata:
  - dataset version,
  - manifest version,
  - answer model,
  - judge model,
  - git revision,
  - dirty flag.

### Alert/gate conditions
- Missing required source family or security slice.
- Any hard-threshold security metric failure.
- Missing artifact sections for screening or suspicious events.
- Judge lane requested but unavailable.
- Unbounded sensitive-context exposure in artifacts.

## 11. Test Plan

### Unit and contract coverage
Planned test files:
- `tests/test_rag_eval_dataset_contract.py`
- `tests/test_rag_eval_fixture_manifest.py`
- `tests/test_rag_eval_service.py`
- `tests/test_rag_eval_baseline_runner.py`
- `tests/test_rag_eval_quality_gate.py`
- `tests/test_rag_eval_security_contract.py`
- `tests/test_rag_eval_trends.py`

Fast tests must validate:
- dataset schema,
- source manifest schema,
- artifact schema,
- trend aggregation,
- gate logic,
- security case contract,
- instruction-plane separation formatting contract.

### Slow local E2E coverage
Planned marker:
- `rag_quality_local`

Planned local E2E file:
- `tests/test_rag_eval_local_e2e.py`

This lane should verify:
- fixture preparation from local paths,
- screening results,
- real KB build,
- retrieval and answer execution,
- judge scoring through Ollama,
- artifact generation,
- baseline vs candidate comparison,
- security summaries and suspicious-event outputs.

### Exact commands for implementation phase
- `python -m py_compile backend/services/rag_eval_service.py tests/test_rag_eval_dataset_contract.py tests/test_rag_eval_fixture_manifest.py tests/test_rag_eval_service.py tests/test_rag_eval_baseline_runner.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_security_contract.py tests/test_rag_eval_trends.py`
- `.venv\Scripts\python.exe -m pytest -q tests/test_rag_eval_dataset_contract.py tests/test_rag_eval_fixture_manifest.py tests/test_rag_eval_service.py tests/test_rag_eval_baseline_runner.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_security_contract.py tests/test_rag_eval_trends.py`
- `.venv\Scripts\python.exe -m pytest -q -m rag_quality_local tests/test_rag_eval_local_e2e.py`
- `.venv\Scripts\python.exe scripts/rag_eval_baseline_runner.py --dataset tests/data/rag_eval_ready_data_v2.yaml --source-manifest tests/data/rag_eval_source_manifest_v1.yaml --label local-ollama`
- `.venv\Scripts\python.exe scripts/rag_eval_quality_gate.py --baseline-report-json <baseline> --run-report-json <candidate>`
- `python scripts/scan_secrets.py`
- `python scripts/ci_policy_gate.py --working-tree`

## 12. Rollout Plan and Rollback Plan

### Rollout
1. Approve this design.
2. Implement `RAGEXEC-008`:
   - dataset v2,
   - source manifest,
   - security case schema,
   - contract tests.
3. Implement `RAGEXEC-009`:
   - answer-level judging,
   - trend artifacts,
   - source-family/security reports.
4. Implement `RAGEXEC-010`:
   - local workflow,
   - CI-safe fast lane,
   - baseline/gate documentation.
5. Use the new metrics to drive `RAGEXEC-013..018`.

### Rollback
- Roll back by reverting the eval-only slice currently being implemented.
- Keep existing retrieval-only eval path functional until the new evaluator is stable.
- If answer-level judge integration proves unstable, retain schema/artifact groundwork but keep judge metrics in warning mode until fixed.
- If local-only corpora integration causes instability, preserve the repo-safe synthetic fixture path and disable external-corpora resolution by configuration rather than deleting the contract.

## 13. Acceptance Criteria Checklist
- [ ] The design defines a versioned dataset contract that supports:
  - source families,
  - answer-level gold facts,
  - refusal cases,
  - noisy-context cases,
  - adversarial/security cases.
- [ ] The design defines a source manifest contract for:
  - repo-safe fixtures,
  - local-only corpora,
  - sensitivity flags,
  - screening profile,
  - materialization/cache behavior.
- [ ] Ollama answer and judge roles are separately configurable through the existing provider stack plus eval-specific overrides.
- [ ] The design defines per-run JSON and Markdown artifact formats plus append-only trend history.
- [ ] The design defines metric families covering:
  - retrieval,
  - answer grounding,
  - context quality,
  - refusal/citation control,
  - security/resilience.
- [ ] Security is a required gateable outcome:
  - prompt injection resistance,
  - indirect injection resistance,
  - system-prompt leak blocking,
  - secret leak blocking,
  - screening outcomes,
  - suspicious-event observability,
  - instruction-plane separation.
- [ ] The design aligns with `RAGEXEC-008..018` rather than introducing a parallel roadmap.
- [ ] The design keeps real developer-local corpora strictly local-only and forbids embedding their contents in committed code, tests, docs, or artifacts.

## 14. Spec and Documentation Update Plan
Implementation phase must update:
- `SPEC.md`
- `docs/REQUIREMENTS_TRACEABILITY.md`
- `docs/TESTING.md`
- `docs/OPERATIONS.md`
- `docs/CONFIGURATION.md`

Implementation phase may also require updates to:
- `docs/USAGE.md` if local developer workflow documentation belongs there
- `env.template` for eval-specific environment variables
- `README.md` if the local quality workflow becomes a top-level developer entrypoint

## 15. Secret-Safety Impact
- Secrets could appear in:
  - Ollama endpoint configuration,
  - local Telegram export content,
  - retrieved documents,
  - generated artifacts,
  - logs.
- Leakage is prevented by:
  - local-only handling for private corpora,
  - no raw Telegram export commits,
  - bounded artifact excerpts,
  - explicit leak-probe cases,
  - hard-gate metrics for prompt/system/secret leakage,
  - no credential logging,
  - secret scan before completion of implementation slices.

## 16. Phased Implementation Plan Aligned With Current Backlog

### Phase A: `RAGEXEC-008`
- Add dataset v2 and source manifest.
- Add source-family coverage and security/adversarial case taxonomy.
- Add contract tests.

### Phase B: `RAGEXEC-009`
- Extend evaluator to answer-level scoring and Ollama judge integration.
- Write source-family/security-aware trend artifacts and summaries.

### Phase C: `RAGEXEC-010`
- Add fast and slow test lanes.
- Document local corpus/Ollama setup and baseline workflow.
- Gate on missing artifacts and security regressions.

### Phase D: `RAGEXEC-013..015`
- Use the evaluator to improve:
  - canonical chunk contract,
  - PDF/DOCX structure fidelity,
  - web/wiki/code normalization,
  - chat-export structure handling,
  - ingestion-time malicious-document screening.

### Phase E: `RAGEXEC-016..017`
- Use source-family and security metrics to refine:
  - evidence-pack context assembly,
  - context inclusion diagnostics,
  - minimal sensitive-context inclusion.

### Phase F: `RAGEXEC-018`
- Finalize near-ideal thresholds by source family and security scenario.
- Promote stable judge metrics from trend-only to hard-gate where justified.

## Approval
- Approved by user on 2026-03-08 with token `APPROVED:v1`.
- Implementation note: local corpora are verification-only and must never be committed or hardcoded.
