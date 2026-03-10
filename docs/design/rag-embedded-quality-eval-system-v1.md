# Design: Embedded Local RAG Quality-Evaluation System v1

Date: 2026-03-08
Owner: codex (architect)
Status: OUTDATED — superseded by `docs/design/rag-embedded-quality-eval-security-system-v1.md`

Related existing artifacts:
- `docs/design/rag-quality-metrics-contract-v1.md`
- `docs/design/rag-eval-ready-corpus-v1.md`
- `docs/design/rag-eval-baseline-runner-v1.md`
- `docs/design/rag-eval-threshold-gate-workflow-v1.md`
- `docs/design/rag-near-ideal-task-breakdown-v1.md`

## Purpose
- This draft is retained for history only.
- Do not implement from this file.
- Add an embedded, repeatable local quality-evaluation system for the project RAG stack.
- Evaluate not only retrieval, but also answer quality, groundedness, refusal behavior, and source-family regressions.
- Use real project knowledge sources:
  - `test.pdf`
  - `open-harmony` catalog
  - Telegram export data
- Use local Ollama models for answer-generation verification and LLM-as-a-judge scoring.
- Persist trend artifacts so each implementation slice can show measured quality deltas over time.

## Scope
- Dataset contract for committed-safe and local-only corpora.
- Ingestion and evaluation workflow for local repeatable runs.
- Metric contract:
  - lecture minimum metrics,
  - additional project-relevant grounded-answer metrics.
- Ollama configuration contract for answer and judge roles.
- Report artifact and trend storage design.
- Automation and test integration design.
- Phase plan aligned with existing `RAGEXEC-008..018`.

## Out of Scope
- Runtime implementation changes in this cycle.
- Replacing the production RAG stack.
- Committing raw private Telegram export data.
- Final threshold tuning before baseline data is collected.

## Current Baseline
- Current evaluation is centered in `backend/services/rag_eval_service.py`.
- It executes retrieval-only checks over a fixed YAML suite.
- Existing metrics:
  - `recall_at_10`
  - `mrr_at_10`
  - `ndcg_at_10`
- Existing report/gate scripts:
  - `scripts/rag_eval_baseline_runner.py`
  - `scripts/rag_eval_quality_gate.py`
- Current slices are query-shape oriented (`ru`, `en`, `mixed`, `factoid`, `howto`, `legal`, `numeric`, `long-context`) but not source-family oriented.

## Problem Statement
The current eval system is necessary but not sufficient for near-ideal RAG work:

1. It measures retrieval but not generated answers.
2. It does not represent the actual uploaded-knowledge mix.
3. It does not isolate regressions by source family.
4. It does not produce durable per-run trend history for “quality growth by commit”.
5. It does not support local Ollama judge workflows.

## Source Inputs

### Repo-safe input
- `test.pdf`
  - committed file in repo root,
  - suitable for PDF parsing, chunking, numeric/factoid and long-context cases.

### Local external inputs
- `open-harmony` catalog
  - accessible locally in the current session,
  - contains mixed documentation/code structure,
  - useful for code-term, path, API-name, and build/how-to retrieval.
- Telegram export:
  - developer-local export resolved only through local env override
  - useful for chat-style, colloquial, multilingual, and URL-heavy knowledge cases.

### Data safety rule
- Raw Telegram export must remain local-only.
- No raw private export file should be committed into the repository.
- Any committed Telegram-derived fixture must be sanitized and minimized.

## Design Principles
1. Measure the real product path.
   - Answer generation must exercise the actual RAG route, retrieval, context assembly, prompt, and safety logic.

2. Split committed and local-only fixtures.
   - CI-safe suite uses committed-safe subsets.
   - Full local suite may use env-resolved local corpora, but committed artifacts must never embed developer-local absolute paths.

3. Keep evaluation deterministic where possible.
   - Retrieval metrics are deterministic.
   - Ollama judge metrics use pinned model ids, temperature `0`, and fixed prompt contract.

4. Make regressions attributable.
   - Metrics must be sliceable by source family, query intent, and failure mode.

5. Keep the trend history auditable.
   - Every run artifact must record model ids, dataset version, source manifest version, and git context.

## Security Requirements
Security is a first-class requirement of the quality-evaluation system, not a later hardening note.

### Threat model
The evaluator must explicitly cover:
- direct prompt injection in user queries,
- indirect prompt injection / RAG poisoning in indexed documents,
- confidential-data leakage from retrieved sources,
- system-prompt or hidden-policy leakage,
- infrastructure access abuse through overly broad fixture path or KB scope,
- unsafe ingestion of malicious or poisoned source documents.

### Mandatory controls
The design requires the future implementation to enforce and evaluate:
1. Strict instruction-plane separation.
   - System instructions, user query, retrieved context, and judge rubric must be distinct inputs/blocks.
   - Retrieved documents must never be allowed to override system instructions.

2. Ingestion-time document screening.
   - Documents must be screened for suspicious instruction-like payloads, exfiltration attempts, credential bait, or clearly malicious content.
   - Screening outcomes must be visible in artifacts as `accepted`, `flagged`, or `quarantined`.

3. Limited sensitive-context inclusion.
   - Only the minimal evidence required to answer a case may be included in answer context.
   - Sensitive/private corpora must support redaction or bounded-evidence policies.

4. Prompt-leak and secret-leak resistance.
   - The system must refuse requests to reveal hidden prompts, system instructions, credentials, or unrelated private data.

5. Infrastructure access control.
   - Local eval must be restricted to allowlisted fixture paths and dedicated eval KB resources.
   - Eval runs must never require access to production KBs or arbitrary host paths.

6. Suspicious-event observability.
   - Suspicious queries, suspicious documents, suspicious answers, and leakage-block events must be captured as first-class report artifacts.

### Security report contract
Every full eval run must be able to answer:
- Was a suspicious query detected and how was it handled?
- Was a suspicious/poisoned document screened, flagged, or quarantined?
- Did the answer leak hidden/system instructions?
- Did the answer expose sensitive context not required by the case?
- Did the answer follow system instructions instead of adversarial retrieved text?

## Dataset Contract

### Logical split
The evaluation data model is split into two files:

1. `tests/data/rag_eval_ready_data_v2.yaml`
- committed case definitions,
- expected evidence / answer contract,
- source-family tags,
- safe committed fixture references.

2. `tests/data/rag_eval_source_manifest_v1.yaml`
- source fixture definitions,
- path resolution mode,
- local-only vs committed-safe classification,
- sanitization flags,
- expected ingestion mode.

### Source manifest contract
Each fixture entry must define:
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
  - `web/wiki` later
- optional:
  - `checksum`
  - `notes`
  - `prepare_step`

### Test-case contract
Each eval case must define:
- `id`
- `query`
- `source_family`
- `expected_sources`
  - one or more logical source ids or source-path matchers
- `expected_snippets`
  - one or more evidence strings expected in retrieved chunks
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
  - query slices such as `ru`, `en`, `mixed`, `factoid`, `howto`, `definition`, `numeric`, `long_context`

Optional fields:
- `gold_answer`
- `gold_facts`
  - atomic claims expected in a correct answer
- `required_context_entities`
- `allowed_urls`
- `allowed_commands`
- `noise_fixture_ids`
  - distractor fixtures used for noise-sensitivity checks
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
  - `suspicious_query`
  - `suspicious_document`
  - `suspicious_answer`
  - `screened_document`
- `notes`

### Contract invariants
- Every case must map to at least one fixture from the source manifest.
- Every `refusal` case must include an explicit rationale note or negative expectation marker.
- Every Telegram-derived case must reference sanitized/local-only material, not raw export content committed into the repo.
- Every noisy-context case must name its distractor fixture ids explicitly.
- Every adversarial case must declare:
  - `security_expectation`
  - `attack_type`
  - expected observability flags.

## Fixture Strategy

### Committed-safe fixtures
Use committed-safe data for:
- `test.pdf`
- small curated or synthetic subsets derived from:
  - `open-harmony`
  - Telegram export

These fixtures support:
- contract tests,
- CI-safe smoke quality checks,
- review reproducibility.

### Local-only fixture materialization
The full local quality suite may use:
- `RAG_EVAL_LOCAL_OPENHARMONY_PATH`
- `RAG_EVAL_LOCAL_TELEGRAM_EXPORT_PATH`

These inputs are materialized into a gitignored local cache, for example:
- `data/rag_eval_local/fixtures/open_harmony/...`
- `data/rag_eval_local/fixtures/telegram_export/...`

Materialization rules:
- extract only the subset required for eval cases,
- compute checksums and normalized manifests,
- avoid storing unrelated Telegram history in generated artifacts,
- preserve stable logical ids so trend comparisons remain meaningful.
- run ingestion-time screening before fixtures become eligible for eval ingestion,
- preserve screening verdicts in the local cache manifest.

## Ingestion and Evaluation Workflow

### Phase 0: Source preparation
1. Resolve fixture paths from the source manifest.
2. Build local sanitized/materialized cache for external sources when needed.
3. Validate checksums, schema, and required fixture presence.
4. Run fixture/document screening and record verdicts before ingestion.

### Phase 1: Ephemeral knowledge-base build
1. Create or reuse a dedicated eval KB/collection with a deterministic label.
2. Ingest fixtures through the same ingestion/service routes used by the product:
   - document path for PDF
   - code-path ingestion for code/docs tree
   - chat-export ingestion for Telegram JSON
3. Persist manifest hash and ingestion summary for the run.

### Phase 2: Retrieval scoring
For each case:
1. run the actual retrieval path,
2. collect retrieved candidates and diagnostics,
3. compute retrieval metrics and source-family slices.

### Phase 3: Answer scoring
For each non-skipped case:
1. run the actual RAG answer path,
2. capture:
   - final answer,
   - selected context,
   - retrieval diagnostics,
   - grounded URLs / commands in answer,
   - suspicious-query / suspicious-answer flags,
   - prompt/context block structure metadata.

### Phase 4: Judge scoring
For each case requiring answer-level evaluation:
1. call local Ollama judge model with:
   - user query,
   - selected context,
   - generated answer,
   - optional `gold_facts`,
   - optional `gold_answer`,
   - case rubric.
2. compute per-case judge metrics.
3. store reasons/explanations in report artifacts.

### Phase 5: Artifact and trend persistence
1. write per-run JSON artifact,
2. write Markdown summary,
3. append trend row,
4. compare against latest baseline / selected baseline label,
5. emit gate PASS/FAIL + delta summary.
6. persist security-observability summary for suspicious queries/documents/answers.

## Ollama Configuration Contract

### Reuse existing config
The implementation should reuse existing project transport defaults:
- `OLLAMA_BASE_URL`
- `OLLAMA_MODEL`
- `OLLAMA_FILTER_THINKING`

### Add eval-specific overrides
The design requires dedicated eval knobs:
- `RAG_EVAL_PROVIDER=ollama`
- `RAG_EVAL_OLLAMA_BASE_URL`
  - default: `OLLAMA_BASE_URL`
- `RAG_EVAL_ANSWER_MODEL`
  - default: current app answer model under test
- `RAG_EVAL_JUDGE_MODEL`
  - default: explicit pinned judge model, not implicit app default
- `RAG_EVAL_JUDGE_TEMPERATURE`
  - default `0`
- `RAG_EVAL_JUDGE_MAX_RETRIES`
- `RAG_EVAL_ENABLE_LLM_JUDGE`
- `RAG_EVAL_TIMEOUT_SECONDS`
- `RAG_EVAL_LOCAL_OPENHARMONY_PATH`
- `RAG_EVAL_LOCAL_TELEGRAM_EXPORT_PATH`

### Model-role policy
- `answer_model` measures the system under test.
- `judge_model` scores outputs deterministically.
- Using the same model for both roles is allowed but not preferred.
- Judge runs must persist both model ids in artifacts.

## Metric Contract

### A. Retrieval metrics
These remain mandatory and deterministic:
- `recall_at_10`
- `mrr_at_10`
- `ndcg_at_10`
- `source_hit_at_10`
  - at least one expected source appears in top-10 retrieval candidates.
- `evidence_in_context_recall`
  - at least one expected snippet survives into the context actually sent to answer generation.

### A2. Security metrics
These are first-class metrics, not supplemental notes:
- `prompt_injection_resistance`
  - adversarial query failed to override system behavior.
- `indirect_injection_resistance`
  - poisoned retrieved document failed to override system behavior.
- `system_prompt_leak_block_rate`
  - system/hidden instructions were not disclosed.
- `secret_leak_block_rate`
  - sensitive tokens, secrets, or unrelated private data were not disclosed.
- `sensitive_context_overexposure_rate`
  - lower is better; measures excess sensitive context included in prompt/answer artifacts.
- `screening_recall`
  - malicious/poisoned documents were flagged by screening.
- `screening_precision`
  - benign documents were not over-flagged excessively.
- `suspicious_query_flag_recall`
  - suspicious queries were detected when expected.
- `suspicious_document_flag_recall`
  - suspicious documents were flagged when expected.
- `suspicious_answer_flag_recall`
  - suspicious or leaking answers were flagged when expected.
- `instruction_plane_separation_compliance`
  - prompt packaging preserved clear separation between system instructions, user query, and retrieved context.

### B. Lecture minimum answer metrics
These come from the lecture set and should be implemented as answer-level metrics:
- `faithfulness`
  - every answer claim should be supported by selected context.
- `context_precision`
  - retrieved/selected context should be relevant to the query.
- `context_recall`
  - the selected context should contain the facts needed for a correct answer.
- `response_relevancy`
  - the answer should address the actual user question directly.
- `noise_sensitivity`
  - answer quality degradation when distractor context is introduced.

### C. Additional relevant metrics
These should be added because they are directly useful for this project’s RAG quality loop:
- `answer_correctness`
  - judge score against `gold_answer` and/or `gold_facts`.
- `context_relevance`
  - chunk-level context relevance summary, useful when retrieval returns partially noisy evidence.
- `response_groundedness`
  - judge score focused on whether the final response stays within retrieved evidence.
- `exact_match`
  - for strict factoid/numeric outputs where one exact target value is expected.
- `string_presence`
  - for must-mention terms such as package names, function names, config keys, error codes.
- `citation_validity`
  - all surfaced paths/URLs must be backed by retrieved sources or case allowlist.
- `refusal_precision`
  - when the answer should refuse, the system must not fabricate a grounded answer.
- `refusal_recall`
  - when evidence is absent, the system should refuse instead of improvising.
- `grounded_url_precision`
  - URLs in answer must be grounded in retrieved sources or case allowlist.
- `grounded_command_precision`
  - commands in answer must be grounded in selected context for command-bearing cases.
- `answer_latency_ms`
  - per-case runtime for answer generation.
- `judge_latency_ms`
  - per-case runtime for judge scoring.

### D. Gate policy

#### Hard-threshold metrics
These are suitable for hard gates because they are deterministic or close to deterministic:
- `recall_at_10`
- `mrr_at_10`
- `ndcg_at_10`
- `source_hit_at_10`
- `evidence_in_context_recall`
- `citation_validity`
- `refusal_precision`
- `refusal_recall`
- `grounded_url_precision`
- `grounded_command_precision`
- `exact_match` for strict numeric/factoid slices
- `prompt_injection_resistance`
- `indirect_injection_resistance`
- `system_prompt_leak_block_rate`
- `secret_leak_block_rate`
- `instruction_plane_separation_compliance`

#### Baseline-delta metrics
These should use baseline comparison first because they are judge-model sensitive:
- `faithfulness`
- `context_precision`
- `context_recall`
- `response_relevancy`
- `noise_sensitivity`
- `answer_correctness`
- `context_relevance`
- `response_groundedness`
- `screening_precision`
- `sensitive_context_overexposure_rate`

### E. Required slice dimensions
Every run must be sliceable by:
- query language:
  - `ru`
  - `en`
  - `mixed`
- query type:
  - `factoid`
  - `definition`
  - `howto`
  - `numeric`
  - `long_context`
  - `refusal_expected`
- source family:
  - `pdf`
  - `open_harmony_docs`
  - `open_harmony_code`
  - `telegram_chat`
  - later `wiki`
- security scenario:
  - `benign`
  - `direct_injection`
  - `indirect_injection`
  - `prompt_leak_probe`
  - `secret_leak_probe`
  - `access_scope_probe`

## Automation and Test Integration

### Fast `pytest` lane
Fast tests should validate:
- dataset schema,
- source manifest schema,
- artifact schema,
- trend aggregation and gate logic,
- report rendering,
- judge prompt contract formatting,
- instruction-plane separation contract,
- security case schema and observability schema.

These tests must run without Ollama and without private local corpora.

### Slow local `pytest` lane
A dedicated marker should cover full local quality runs, for example:
- `pytest -m rag_quality_local`

This lane should:
- require Ollama reachability,
- optionally prepare local fixtures from external paths,
- build the eval KB,
- run retrieval + answer + judge scoring,
- write artifacts,
- assert gate results against the chosen baseline,
- assert security metrics and suspicious-event artifacts for adversarial cases.

### CLI wrappers
Human-friendly scripts should wrap the same service used by tests:
- prepare fixtures,
- run baseline/candidate evaluation,
- compare trend deltas,
- print Markdown summary and report paths.

The CLI and `pytest` lanes must share the same underlying evaluator to avoid dual logic.

### CI strategy
- CI-safe lane:
  - committed-safe fixtures only,
  - fast contract tests,
  - retrieval and artifact logic,
  - optional smoke answer/judge run if a self-hosted Ollama runner exists,
  - mandatory adversarial mini-suite for injection/leakage regressions when committed-safe fixtures are available.
- Local full-quality lane:
  - external corpora,
  - full Ollama judging,
  - trend persistence across commits,
  - security observability artifacts.

## Artifact Storage

### Committed inputs
- `tests/data/rag_eval_ready_data_v2.yaml`
- `tests/data/rag_eval_source_manifest_v1.yaml`
- small committed-safe fixtures under `tests/fixtures/rag_eval/` if needed

### Local generated inputs
- `data/rag_eval_local/fixtures/...`
- `data/rag_eval_local/kb_cache/...`

These paths should be gitignored.

### Run artifacts
Extend the current artifact story under `data/rag_eval_baseline/`:
- `data/rag_eval_baseline/runs/<label>/<timestamp>_<run_id>.json`
- `data/rag_eval_baseline/runs/<label>/<timestamp>_<run_id>.md`
- `data/rag_eval_baseline/latest/<label>.json`
- `data/rag_eval_baseline/latest/<label>.md`
- `data/rag_eval_baseline/trends/<label>.jsonl`

### Required run metadata
Every JSON trend/run artifact must include:
- `run_id`
- `dataset_version`
- `source_manifest_version`
- `answer_model`
- `judge_model`
- `ollama_base_url`
- `git_sha` or `unknown`
- `git_dirty`
- `started_at`
- `finished_at`
- per-slice metrics
- per-case failures with reasons
- security summary:
  - suspicious query counts,
  - suspicious document counts,
  - suspicious answer counts,
  - screening verdict counts,
  - leakage-block counts

## Risks and Mitigations

### Risk: private data leakage from Telegram export
- Mitigation:
  - raw export remains local-only,
  - committed fixtures must be sanitized,
  - reports should store minimal case evidence, not arbitrary chat dumps.

### Risk: prompt injection or poisoned context degrades the evaluator itself
- Mitigation:
  - strict prompt block separation,
  - dedicated adversarial cases,
  - screening before ingestion,
  - explicit injection-resistance metrics and gates.

### Risk: evaluator leaks system prompt or secrets while judging
- Mitigation:
  - do not include hidden/system prompts in human-readable artifacts,
  - bound judge inputs,
  - add prompt-leak and secret-leak probe cases,
  - keep leak-block metrics as hard-gate signals.

### Risk: eval infrastructure scope is too broad
- Mitigation:
  - allowlisted fixture roots only,
  - dedicated eval KB only,
  - no production KB access,
  - explicit access-scope probe cases and observability.

### Risk: judge instability or bias
- Mitigation:
  - pinned judge model,
  - temperature `0`,
  - explicit rubric prompts,
  - baseline-delta policy for judge-heavy metrics,
  - persist reasons for failed judgments.

### Risk: slow local runs
- Mitigation:
  - split fast and slow lanes,
  - allow per-source-family and per-slice filtering,
  - cache materialized fixtures and prepared eval KBs where safe.

### Risk: CI cannot access external corpora
- Mitigation:
  - committed-safe subset remains mandatory,
  - full local suite is optional but first-class for developer iteration.

### Risk: evaluation bypasses real product behavior
- Mitigation:
  - answer generation must use the real RAG route/path,
  - not a simplified direct prompt against hand-selected chunks.

## Phased Implementation Plan

### Phase A: Dataset and fixture contract
Backlog alignment:
- `RAGEXEC-008`

Deliverables:
- dataset v2 schema,
- source manifest,
- committed-safe fixture subset,
- local-only path overrides,
- source-family coverage tests,
- adversarial/security case taxonomy,
- screening manifest fields.

### Phase B: Answer-level metrics and trend persistence
Backlog alignment:
- `RAGEXEC-009`

Deliverables:
- answer-generation evaluation on top of current retrieval eval,
- Ollama judge integration,
- source-family reports,
- append-only trend history,
- per-run Markdown/JSON summaries,
- security metric aggregation and suspicious-event reports.

### Phase C: Local test workflow and CI-safe gate
Backlog alignment:
- `RAGEXEC-010`

Deliverables:
- `pytest` fast/slow lanes,
- local runbook,
- CI-safe subset gate,
- documentation for baseline refresh and trend interpretation,
- injection/leakage regression lane and observability expectations.

### Phase D: Ingestion hardening measured by the new system
Backlog alignment:
- `RAGEXEC-013`
- `RAGEXEC-014`
- `RAGEXEC-015`

Focus:
- PDF parser fidelity,
- canonical chunk structure,
- code/docs/chat structural metadata,
- source-family regressions visible per slice,
- poisoned-document screening and sensitive-data tagging preserved through ingestion.

### Phase E: Context composer quality loop
Backlog alignment:
- `RAGEXEC-016`
- `RAGEXEC-017`

Focus:
- evidence-pack assembly,
- context inclusion diagnostics,
- measured gains in:
  - `evidence_in_context_recall`
  - `faithfulness`
  - `noise_sensitivity`
  - `response_relevancy`
  - reduced `sensitive_context_overexposure_rate`
  - stable injection resistance under richer context assembly

### Phase F: Near-ideal thresholds
Backlog alignment:
- `RAGEXEC-018`

Focus:
- finalize source-family thresholds,
- switch more answer-level metrics from warning/baseline-only to hard-gate where stable,
- define the near-ideal local acceptance profile,
- finalize security gates for leakage/injection/screening observability.

## Initial Acceptance Criteria for the Design
1. The future evaluator can run on real project corpora without external cloud services.
2. The dataset contract explicitly supports:
   - source families,
   - refusal cases,
   - noisy-context cases,
   - answer-level gold facts,
   - adversarial/security cases.
3. Ollama answer and judge roles are separately configurable.
4. Trend artifacts can show quality growth or regression per run.
5. The design fits the existing `RAGEXEC-008..018` roadmap without requiring a parallel eval stack rewrite.
6. Security is a required output of the evaluator:
   - injection resistance,
   - leak blocking,
   - screening verdicts,
   - suspicious-event observability,
   - instruction-plane separation.

## Metrics Rationale References
- RAGAS available metrics:
  - `https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/`
- TruLens RAG triad:
  - `https://www.trulens.org/getting_started/core_concepts/rag_triad/`
- DeepEval framework overview for pytest-native LLM evaluation patterns:
  - `https://deepeval.com/`

## Approval
REVIEW REQUIRED — Reply `APPROVED:v1` or `CHANGES:<bullets>`
