# RAG Evidence Pack and Context Budget Policy v1

Date: 2026-03-19
Status: draft for approval
Task: `RAGSVC-011`

## 1. Summary

### Problem statement
The current architecture correctly treats context composition as a separate stage, but it still lacks an explicit operator-facing contract for context budgets:
- how much evidence is allowed,
- when one family should dominate,
- when cross-family expansion is justified,
- when full-context behavior is acceptable.

Without this contract, context quality regresses silently and LLM behavior gets blamed for retrieval failures.

### Goals
- Define a deterministic evidence-pack contract.
- Make context-window constraints explicit and realistic.
- Introduce controlled non-default modes for full-context and debug usage.

### Non-goals
- No increase in default context size just for convenience.
- No LLM prompt redesign.
- No agentic retrieval in this slice.

## 2. Scope Boundaries

### In scope
- evidence-pack assembly,
- token/character budgeting policy,
- family-boundary rules,
- full-context mode rules,
- diagnostics for inclusion/exclusion decisions.

### Out of scope
- chunk parser changes,
- reranker design,
- answer formatting.

## 3. Assumptions and Constraints

- Context window is always smaller than the full relevant corpus.
- The system must optimize for grounded evidence, not maximum raw context volume.
- Full-context mode is a niche operator/debug tool, not the normal answer path.

## 4. Architecture

### 4.1 Evidence-pack concept
An evidence pack is the bounded, ordered set of chunks/sections passed to answer generation.

It should contain:
- one anchor family,
- anchor chunks from the winning family,
- optional support chunks from the same family,
- optional cross-family evidence only when coverage gap is explicit.

### 4.2 Budget dimensions
The policy must define at least:
- max total context units,
- max anchor-family share,
- max cross-family share,
- max support-neighbor expansion,
- min anchor coverage before cross-family expansion.

### 4.3 Context modes

#### `standard`
Default mode.
- bounded evidence pack
- one family first
- cross-family expansion only on explicit gap

#### `tight`
For exact lookup/reference cases.
- minimal anchor set
- almost no cross-family expansion

#### `broad_procedural`
For multi-step procedures.
- allows more same-family neighbor support
- still limits unrelated-family drift

#### `full_context_debug`
Not for default production answers.
- allowed only for small sources or explicit debug/manual validation
- must be surfaced in diagnostics and logs

### 4.4 Full-context rules
Full-context mode should be allowed only when:
- the source is small enough,
- the operator explicitly requested it,
- diagnostics clearly mark it,
- it is not silently chosen as fallback for normal traffic.

## 5. Interfaces and Contracts

### Runtime contract
Possible additive setting:
- `RAG_CONTEXT_POLICY=standard|tight|broad_procedural|full_context_debug`

### Diagnostics contract
Must expose:
- `context_policy`
- anchor family id
- included rows with reasons
- excluded rows with reason
- whether full-context mode was used

## 6. Data Model Changes

No DB schema change required by default.

## 7. Edge Cases and Failure Modes

- Too-tight policy may exclude necessary context.
  - support-chunk inclusion rules must remain measurable.
- Too-broad policy may pollute answers.
  - family-boundary limits must remain strict.
- Full-context mode can hide retrieval problems.
  - it must never be the silent default.

## 8. Security Requirements

- Full-context mode must not expand unrelated sensitive content beyond the current allowed retrieval scope.
- Diagnostics must not dump unsafe raw context beyond current policy.

## 9. Performance Requirements

- Evidence-pack assembly must remain deterministic and bounded.
- No mode may exceed configured safe context budgets without explicit operator override.

## 10. Observability

For every request, it should be clear:
- which context policy was active,
- what the anchor family was,
- why chunks were added or excluded,
- whether a debug/full-context mode was used.

## 11. Test Plan

### Unit tests
- anchor-first packing
- same-family support expansion
- cross-family gap-triggered inclusion
- full-context mode restriction

### Integration tests
- diagnostics exposure
- fallback using the same family boundary

### Local validation
- compare current failing exact-lookup and broad-procedural cases under `tight` vs `broad_procedural`

## 12. Rollout and Rollback

### Rollout
- codify current default as `standard`,
- add explicit `tight` and `broad_procedural`,
- add `full_context_debug` as opt-in only.

### Rollback
- revert to current deterministic default path and keep diagnostics additive.

## 13. Acceptance Criteria

- Context assembly is defined as an explicit policy contract.
- Full-context mode exists only as a controlled debug/manual mode.
- Diagnostics explain context inclusion/exclusion behavior.

## 14. Spec and Doc Update Plan

Implementation must update:
- `SPEC.md`
- `docs/REQUIREMENTS_TRACEABILITY.md`
- `docs/CONFIGURATION.md`
- `docs/OPERATIONS.md`
- `docs/TESTING.md`

No spec update is required in this design-only cycle.

## 15. Secret-Safety Impact

- Context debug/full-context modes must not weaken current secret-safety and refusal behavior.

## Approval

APPROVED:v1

Implementation note (arch-review 2026-03-19):
- When `RAG_RETRIEVAL_PROFILE` and `RAG_CONTEXT_POLICY` are both set, context policy governs packing behavior and the retrieval profile governs candidate budgets/rerank window independently. No silent override: if a context policy is incompatible with the active retrieval profile, it must surface in diagnostics.
- `full_context_debug` mode must never be the silent default; it requires explicit operator flag and must always appear in diagnostics and logs.
