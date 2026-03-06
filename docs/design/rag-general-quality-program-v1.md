# Design: RAG General Quality Program v1

Date: 2026-03-06  
Type: Architecture + execution program (generalized, non-domain-specific)

## Goal
- Improve RAG answer quality to stable "good by default" level without query/domain hardcoding.
- Make quality measurable and protected by mandatory regression gates.

## Non-Goals
- No query rules specific to OpenHarmony/OHOS or any single knowledge domain.
- No one-shot large refactor without intermediate quality checkpoints.

## Architecture Changes (General)
1. Ingestion normalization:
   - unify `source_path/doc_title/section_path/chunk_kind` semantics across loaders;
   - preserve command/code chunks and document structure.
2. Retrieval generalization:
   - keep hybrid retrieval (dense + lexical) with stable rerank top-N;
   - remove fragile route-level query heuristics where possible.
3. Context assembly:
   - select context by ranked evidence quality and budget, not by ad-hoc template logic.
4. Prompting:
   - concise direct answer format without forced template headings;
   - extractive-first behavior with strict grounding in context.
5. Safety/postprocess:
   - keep hallucination guards but avoid over-aggressive command removal.
6. Evaluation and CI gates:
   - fixed regression corpus (ready data) + source-hit and grounding checks;
   - fail pipeline on quality regressions.

## Mandatory Workflow Per Step
For every implementation step:
1) implement minimal scoped change,
2) run focused tests + mandatory gates,
3) run reviewer pass,
4) create one atomic commit with clear result.

Rule: one completed step == one commit. No multi-step mixed commits.

## Step-by-Step Backlog (atomic)

### Phase 0 - Baseline and Evaluation Harness
- Step P0-1: define quality metrics and thresholds (`source_hit_rate`, `grounded_answer_rate`, command-preservation checks).
- Step P0-2: add fixed evaluation dataset (golden questions + expected sources/snippets).
- Step P0-3: add evaluation runner and report artifact.
- Step P0-4: integrate quality gate script with threshold-based PASS/FAIL.

Commit outputs:
- one commit per step (`P0-1` .. `P0-4`).

P0-1 artifact:
- `docs/design/rag-quality-metrics-contract-v1.md`.

P0-2 artifact:
- `docs/design/rag-eval-ready-corpus-v1.md`.

P0-3 artifact:
- `docs/design/rag-eval-baseline-runner-v1.md`.

### Phase 1 - Ingestion Metadata Quality
- Step P1-1: normalize metadata contract across all loaders.
- Step P1-2: harden markdown/code chunk metadata consistency.
- Step P1-3: add ingestion regression tests for metadata and update semantics.

Commit outputs:
- one commit per step (`P1-1` .. `P1-3`).

### Phase 2 - Retrieval Generalization
- Step P2-1: remove/limit brittle route-level query-specific boosts.
- Step P2-2: stabilize hybrid candidate fusion + rerank selection boundaries.
- Step P2-3: add retrieval diagnostics assertions in tests (candidate quality visibility).

Commit outputs:
- one commit per step (`P2-1` .. `P2-3`).

### Phase 3 - Prompt and Answer Formatting
- Step P3-1: simplify answer prompt format (no forced "Main Answer/Additionally Found").
- Step P3-2: align RU/EN answer behavior around direct grounded response.
- Step P3-3: add response-format regression tests.

Commit outputs:
- one commit per step (`P3-1` .. `P3-3`).

### Phase 4 - Safety and Postprocessing
- Step P4-1: relax command sanitizer to token-level validation, not full-line destructive matching.
- Step P4-2: keep URL trust filtering but prevent removal of valid context-backed links.
- Step P4-3: add positive/negative safety regression tests.

Commit outputs:
- one commit per step (`P4-1` .. `P4-3`).

### Phase 5 - End-to-End Regression and CI Gate
- Step P5-1: add end-to-end RAG regression suite on fixed corpus.
- Step P5-2: wire mandatory CI quality gate (fail on metric regressions).
- Step P5-3: add operational docs for running local quality checks and interpreting failures.

Commit outputs:
- one commit per step (`P5-1` .. `P5-3`).

## Mandatory Review and Gate Checklist (every step)
- Reviewer artifact in `coordination/reviews/<step-id>-<date>.md`.
- Required checks:
  - `python -m py_compile <changed_py_files>`
  - `pytest <targeted_tests>`
  - `python scripts/scan_secrets.py`
  - `python scripts/ci_policy_gate.py --working-tree`
- For quality-impacting steps also required:
  - evaluation runner on fixed corpus,
  - quality gate PASS against baseline thresholds.

## Risks and Controls
- Risk: quality regressions hidden by prompt-only fixes.
  - Control: retrieval/source-hit metrics in gate.
- Risk: over-filtering commands/links degrades how-to answers.
  - Control: dedicated command-preservation tests.
- Risk: broad changes become unreviewable.
  - Control: atomic step commits only.

## Rollback Strategy
- Each step is atomic and can be reverted independently.
- If quality gate fails after merge candidate build, rollback latest step commit only.
