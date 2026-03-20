# RAG Ingestion Quality and Parser Degradation v1

Date: 2026-03-19
Status: draft for approval
Task: `RAGSVC-012`

## 1. Summary

### Problem statement
One reason Open WebUI works pragmatically is that it treats many RAG issues as ingestion/context problems, not only retrieval problems. This repository already has strong parser/chunk contracts, but it still lacks one explicit operator-facing design for:
- parser quality expectations,
- degraded extraction modes,
- bad-source reporting,
- when to fix source docs instead of overfitting retrieval.

### Goals
- Define an ingestion quality contract and degradation model.
- Make parser quality observable and actionable.
- Separate “good source, weak retrieval” from “bad source, unrecoverable structure”.

### Non-goals
- No automatic source rewriting.
- No OCR/parser vendor migration decision in this cycle.
- No new parser dependency by default.

## 2. Scope Boundaries

### In scope
- parser quality signals,
- degraded-mode contract,
- operator guidance for bad documents,
- source-quality reporting hooks.

### Out of scope
- runtime retrieval scoring changes,
- answer-generation changes,
- editorial workflow tooling.

## 3. Assumptions and Constraints

- Different source types degrade differently.
- Some documents are intrinsically bad for retrieval.
- The architecture must not silently compensate forever for poor source quality.

## 4. Architecture

### 4.1 Parser outcome classes
Every ingested source should conceptually end in one of these states:

#### `good`
- headings/sections preserved,
- stable page/title info present,
- chunking confidence high.

#### `usable_degraded`
- text extracted,
- some structure lost,
- retrieval still possible but lower confidence.

#### `poor_source`
- structure highly ambiguous or collapsed,
- key artifacts buried or mixed,
- retrieval quality likely poor unless the source is fixed.

#### `failed`
- parser/load failure, empty result, or invalid source.

### 4.2 Source-quality signals
Examples:
- heading density
- section continuity
- lead-paragraph presence
- chunk-size variance extremes
- excessive inventory/table density
- title ambiguity
- parser warnings

### 4.3 Operator guidance
When source quality is poor, the system should guide the operator toward:
- fixing page titles,
- splitting mixed-role pages,
- adding summary paragraphs,
- moving exact patch/setup instructions into dedicated pages/sections.

The system should not default to retrieval hacks for these cases.

## 5. Interfaces and Contracts

### Ingestion contract
Existing parser metadata should be extendable to express:
- parser outcome class
- degradation reason
- source-quality hints

### Ops/reporting contract
Local validation and ingest diagnostics should be able to report:
- parser degraded
- likely mixed-role page
- likely noisy inventory/status page
- likely exact-artifact buried in note page

## 6. Data Model Changes

No schema change is required by default in the design phase.

Potential future additive metadata:
- `parser_outcome`
- `parser_degradation_reason`
- `source_quality_flags`

## 7. Edge Cases and Failure Modes

- A document can be structurally simple but still useful.
  - low heading count is not automatically poor quality.
- A highly technical doc may contain many tables and still be canonical.
  - quality must remain query-aware in later retrieval layers.
- Over-reporting bad-source quality can create operator noise.
  - only strong signals should surface by default.

## 8. Security Requirements

- Ingestion diagnostics must not log sensitive raw content unnecessarily.
- No source-quality report should leak secrets or private paths beyond current diagnostics policy.

## 9. Performance Requirements

- Parser outcome classification must reuse existing extraction metadata where possible.
- No expensive whole-corpus rescoring in the hot query path.

## 10. Observability

Operators should be able to answer:
- was this source parsed cleanly?
- did structure collapse?
- is this likely a retrieval problem or a source-quality problem?

## 11. Test Plan

### Unit tests
- parser outcome classification logic
- degraded-mode mapping by source type

### Integration tests
- ingest diagnostics exposure
- local validation reporting of poor-source cases

### Local validation
- annotate current OpenHarmony and ArkUI misses as:
  - retrieval issue
  - source-quality issue
  - mixed/unclear

## 12. Rollout and Rollback

### Rollout
- first add classification/reporting,
- then integrate reporting into local validation and ops docs.

### Rollback
- reporting is additive and removable independently from parser logic.

## 13. Acceptance Criteria

- Architecture defines parser outcome classes and degraded-mode reporting.
- Operators have guidance for when to fix source docs rather than tune retrieval.
- Future retrieval work can explicitly distinguish source-quality failures from ranking failures.

## 14. Spec and Doc Update Plan

Implementation must update:
- `SPEC.md`
- `docs/REQUIREMENTS_TRACEABILITY.md`
- `docs/OPERATIONS.md`
- `docs/TESTING.md`
- `docs/USAGE.md` if user-facing source guidance is added

No spec update is required in this design-only cycle.

## 15. Secret-Safety Impact

- Any future degraded-source reports must preserve current secret-handling rules.

## Approval

APPROVED:v1

Implementation note (arch-review 2026-03-19):
- Parser quality signals computed at ingestion time (list/table density, heading count, section continuity) must be persisted in chunk metadata so retrieval can read them directly instead of recomputing from raw content at query time. This bridges the gap with contamination-control (RAGSVC-006): ingestion writes `source_quality_flags`, retrieval reads them as an additional contamination signal.
