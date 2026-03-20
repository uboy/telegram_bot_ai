# RAG Retrieval Policy Profiles v1

Date: 2026-03-19
Status: draft for approval
Task: `RAGSVC-010`

## 1. Summary

### Problem statement
The system already exposes many retrieval knobs indirectly:
- dense candidate budget,
- BM25 budget,
- rerank window,
- field-aware retrieval,
- family-aware ranking.

But these knobs are still closer to implementation internals than to an operator-facing retrieval policy. That makes the system harder to tune safely and harder to reason about during regressions.

### Goals
- Define a small set of explicit retrieval policy profiles.
- Make retrieval behavior configurable without turning production tuning into uncontrolled env-sprawl.
- Keep the underlying scoring generic and corpus-agnostic.

### Non-goals
- No removal of low-level tuning knobs.
- No automatic self-tuning in this cycle.
- No LLM-side changes.

## 2. Scope Boundaries

### In scope
- pre-LLM retrieval profiles,
- policy-to-knob mapping,
- diagnostics exposure of active profile,
- rollout and evaluation guidance.

### Out of scope
- answer prompts,
- source parser changes,
- tool-calling retrieval.

## 3. Assumptions and Constraints

- Current low-level retrieval controls remain available for rollback/debug.
- Default behavior must stay explainable and testable.
- Profile count should stay small.

## 4. Architecture

### 4.1 Policy profile concept
Instead of expecting operators to tune several loosely coupled knobs, define named retrieval policies.

Each profile chooses a bounded configuration for:
- dense candidate budget,
- sparse/BM25 budget,
- metadata/field channel budget,
- rerank window,
- family aggregation strength,
- contamination penalty strength,
- exact-lookup routing behavior.

### 4.2 Proposed initial profiles

#### `balanced`
Default profile for mixed corpora.
- balanced dense/sparse mix
- moderate rerank window
- moderate family support
- moderate contamination control

#### `exact_lookup`
For reference/setup/navigation heavy corpora.
- stronger field channel
- stronger exact structural match
- tighter family boundary
- stronger contamination control

#### `procedural`
For build/run/how-to heavy corpora.
- stronger family aggregation
- neighbor continuity emphasis
- broader same-family support expansion
- contamination control focused on troubleshooting drift

#### `large_reference`
For large documentation corpora with many long pages.
- larger candidate window
- stronger canonicality scoring
- tighter context-pack constraints
- stronger archive/status penalties for non-status queries

### 4.3 Operator model
Operators should choose:
- one default policy,
- optional per-KB override,
- optional per-request debug override for testing.

Do not expose ad hoc tuning as the primary user experience.

## 5. Interfaces and Contracts

### Runtime configuration contract
Proposed additive setting:
- `RAG_RETRIEVAL_PROFILE=balanced|exact_lookup|procedural|large_reference`

Optional future per-KB setting:
- stored alongside KB settings, resolved before query execution.

### Diagnostics contract
Expose:
- `retrieval_profile`
- resolved candidate budgets
- resolved rerank window
- whether exact-lookup policy path was active

## 6. Data Model Changes

No DB schema change is required by default.

Optional future addition:
- KB-level retrieval profile in KB settings store.

## 7. Edge Cases and Failure Modes

- Wrong profile can hurt a corpus.
  - every profile change must go through local smoke/eval comparison.
- One corpus can contain both reference and procedural material.
  - `balanced` remains the safe default.
- Per-request override can hide systemic problems.
  - debug override must remain explicit and observable.

## 8. Security Requirements

- No new dependency required.
- No unsafe hidden overrides from user query text.
- Profile selection must come only from trusted config/runtime controls.

## 9. Performance Requirements

- Policy resolution must be cheap and deterministic.
- Profiles must map to bounded hot-path knobs only.
- No profile may trigger unbounded retrieval or full-corpus scans.

## 10. Observability

Every request should make it obvious:
- which profile was active,
- what budgets were resolved,
- whether result quality might be profile-related.

## 11. Test Plan

### Unit tests
- profile resolution
- fallback to default
- invalid profile handling

### Integration tests
- diagnostics exposure
- candidate-budget differences by profile

### Local validation
- compare `balanced` vs `exact_lookup` and `procedural` on current OpenHarmony/ArkUI case sets

## 12. Rollout and Rollback

### Rollout
- land profile abstraction first,
- keep current low-level knobs as underlying source of truth,
- map the current default behavior to `balanced`.

### Rollback
- revert to legacy explicit knob-only behavior if profiles cause confusion or regressions.

## 13. Acceptance Criteria

- The architecture defines a small, explicit set of retrieval profiles.
- Active retrieval profile is observable in diagnostics.
- Policy changes can be evaluated via existing local smoke/eval workflow.

## 14. Spec and Doc Update Plan

Implementation must update:
- `SPEC.md`
- `docs/REQUIREMENTS_TRACEABILITY.md`
- `docs/CONFIGURATION.md`
- `docs/OPERATIONS.md`
- `docs/TESTING.md`

No spec update is required in this design-only cycle.

## 15. Secret-Safety Impact

- No secret-related impact beyond normal config safety.

## Approval

APPROVED:v1

Implementation note (arch-review 2026-03-19):
- Precedence rules must be explicit in implementation: per-request debug override > KB-level profile override > global `RAG_RETRIEVAL_PROFILE` > individual env knobs. Conflicts between policy profiles and context budget policy (`RAG_CONTEXT_POLICY`) must resolve in favor of the more specific axis (profile wins on retrieval budgets/rerank, context policy wins on packing behavior).
