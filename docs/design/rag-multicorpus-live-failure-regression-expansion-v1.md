# RAG Multicorpus Live-Failure Regression Expansion v1

Date: 2026-03-19
Status: draft for approval
Task: `RAGSVC-008`

## 1. Summary

### Problem statement
Focused tests are green, but live local corpora still expose misses. This means current committed eval and regression suites do not fully represent real failure surfaces.

### Goals
- Convert current OpenHarmony and ArkUI live misses into maintained regression assets.
- Separate retrieval failure classes so quality movement is measurable by intent, not only aggregate score.
- Keep committed fixtures public-safe while preserving local-corpus validation value.

### Non-goals
- No private corpus content commitment.
- No answer-lane judge changes in this slice.
- No one-off ad hoc smoke scripts outside the existing framework.

## 2. Scope Boundaries

### In scope
- local smoke case sets,
- committed public-safe eval slices,
- deterministic regression tests for current live failures,
- failure taxonomy updates.

### Out of scope
- new retrieval logic,
- new embeddings/rerankers,
- answer-model benchmarking.

## 3. Assumptions and Constraints

- Local corpora remain developer-local only.
- Existing smoke harness and eval service are reusable.
- Committed fixtures must stay source-safe and path-safe.

## 4. Architecture

### 4.1 Failure taxonomy
Track failures by class:
- exact lookup miss
- navigation/reference miss
- setup canonical-page miss
- contamination/status-page miss
- broad procedure miss
- patch/fix lookup miss

### 4.2 Asset layers

Layer 1. Committed deterministic regressions
- synthetic or public-safe fixtures
- target one retrieval behavior at a time

Layer 2. Local multicorpus smoke
- OpenHarmony and ArkUI real corpora
- extractive mode by default
- source-hit focused reporting

Layer 3. Optional live-backend smoke
- same case sets through current runtime API
- used after implementation slices, not for default CI

### 4.3 Reporting

Each run should report:
- total pass/fail,
- pass/fail by failure class,
- top persistent misses,
- delta vs previous baseline.

## 5. Interfaces and Contracts

### Existing assets to extend
- `tests/data/rag_eval_multicorpus_public_v1.yaml`
- `tests/data/rag_eval_source_manifest_v1.yaml`
- `scripts/wiki_corpus_local_smoke.py`
- local case JSONs under `.scratchpad` for developer-only runs

### Proposed contract additions
- per-case `failure_class`
- optional `expected_family_fragment`
- optional `query_mode`

## 6. Data Model Changes

No DB schema change required.

## 7. Edge Cases and Failure Modes

- One query may legitimately match multiple pages.
  - allow either source-hit family or exact-source-hit according to case type.
- Some live misses are caused by poor source docs rather than retrieval.
  - such cases should be marked separately instead of forcing retrieval overfitting.

## 8. Security Requirements

- Do not commit private local corpus text.
- Keep committed fixtures abstract/public-safe.
- Keep local case files and smoke JSON outputs out of normal public artifact workflows unless sanitized.

## 9. Performance Requirements

- Fast deterministic tests remain network-free.
- Local smoke remains opt-in and temp-DB-based.
- Reporting must be lightweight enough for iterative use.

## 10. Observability

Regression output must say:
- which failure class regressed,
- whether the miss was candidate-generation, ranking, or context-family related,
- whether the query hit the expected family anywhere in top-k.

## 11. Test Plan

### Deterministic tests
- add unit and integration regressions for each failure class represented by current live misses

### Local smoke
- rerun both corpora after each retrieval slice
- compare by failure class, not just total score

### Optional live mode
- verify current backend behavior with same case taxonomy

## 12. Rollout and Rollback

### Rollout
- first extend case taxonomy and reporting,
- then add committed regressions for the strongest recurring miss shapes,
- then widen local smoke summaries.

### Rollback
- tooling/reporting changes are additive and removable independently from retrieval logic.

## 13. Acceptance Criteria

- Current live failure shapes are represented in committed deterministic regressions or explicitly tracked local smoke cases.
- Local smoke reports are broken down by failure class.
- Quality movement can be measured separately for exact lookup, navigation, setup, and contamination problems.

## 14. Spec and Doc Update Plan

Implementation must update:
- `docs/TESTING.md`
- `docs/OPERATIONS.md`
- `docs/REQUIREMENTS_TRACEABILITY.md`

No spec update is required in this design-only cycle.

## 15. Secret-Safety Impact

- Local corpora and raw excerpts remain local-only.
- Public-safe fixtures must not embed private data.

## Approval

APPROVED:v1
